# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
throttle_return_ingest.py — source the return-spring and pedal numbers from REAL
data (a bench log, a CAD upload) instead of a human retyping them.

WHY THIS EXISTS
---------------
Every check in throttle_return.py is only as good as the numbers typed into it.
That transcription step is the last remaining boundary: a mistyped spring rate or
lever arm produces a confident, wrong verdict. This module shrinks that boundary
the ONLY honest way — by reading the numbers off a source of truth:

  1. `spring_rate_from_bench_log()` fits the spring rate k from a real bench
     force/deflection log (the CSV any team can produce with a luggage scale and a
     ruler). The physical spring IS the ground truth — better than a datasheet,
     better than CAD, because it's the actual part on the actual car. The fit
     carries its R² and point count, so a poor/nonlinear fit is visible, not
     hidden.

  2. `crosscheck_pedal_against_cad()` validates the TYPED pedal dimensions against
     the real CAD model's bounding envelope and units (from cad_ingest's IGES
     provenance). It catches the transcription error directly — a lever arm typed
     in inches, an off-by-10× width, a dimension larger than the whole part — by
     comparing against what the CAD says the part actually is.

WHAT THIS DELIBERATELY DOES NOT DO (the boundary that is NOT breakable honestly)
-------------------------------------------------------------------------------
It does NOT invent numbers to make a check runnable. It will not:
  * infer a spring rate from a coil photo or a guessed material,
  * read a pedal's section thickness or weld size out of an IGES header (that data
    isn't in the header — only the bounding box and units are), or
  * fill a missing measurement with a "representative" value and then report a
    confident verdict.
An IGES Start/Global section carries provenance and the model envelope, not the
solid geometry, so a true section-dimension extraction needs a BREP kernel we do
not ship. Where the source of truth is silent, this module returns an honest
"can't confirm" rather than a fabricated number — the same contract as the rest
of KinematiK (see cad_ingest.py, damper.py). Breaking the boundary means removing
the retyping where a real source exists; it does not mean pretending a source
exists where it doesn't.

UNITS: SI out (N/m for rate), mm for the CAD cross-check (to match the pedal UI).
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from .interfaces import Finding, Severity


# --------------------------------------------------------------------------- #
#  1) Spring rate from a real bench force/deflection log
# --------------------------------------------------------------------------- #
@dataclass
class BenchFit:
    """A spring rate fitted from measured (force, deflection) points.

    k_N_per_m   : fitted linear rate (least-squares slope through the data)
    preload_N   : fitted intercept force at zero deflection (seat preload, if any)
    r_squared   : coefficient of determination — how linear the spring really is
    n_points    : how many measured points the fit used
    is_trustworthy : True only if enough points AND a good linear fit
    findings    : typed findings (owned by "brakes") for the board / UI
    """
    k_N_per_m: float
    preload_N: float
    r_squared: float
    n_points: int
    is_trustworthy: bool
    findings: list = field(default_factory=list)

    def as_dict(self):
        d = dict(k_N_per_m=self.k_N_per_m, preload_N=self.preload_N,
                 r_squared=self.r_squared, n_points=self.n_points,
                 is_trustworthy=self.is_trustworthy,
                 findings=[f.as_dict() for f in self.findings])
        return d


# column-name synonyms teams actually use in a bench log
_FORCE_KEYS = ("force", "load", "f", "force_n", "load_n", "newtons", "n", "weight",
               "weight_n")
_DEFL_KEYS = ("deflection", "deflect", "displacement", "disp", "travel", "stretch",
              "x", "extension", "defl_mm", "x_mm", "mm")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())


def _pick_columns(header: list) -> "tuple[Optional[int], Optional[int], bool]":
    """Return (force_idx, defl_idx, defl_in_mm). Best-effort from column names."""
    normed = [_norm(h) for h in header]
    f_idx = d_idx = None
    defl_mm = False
    # Deflection first (more specific tokens), so a "force"/"load" column isn't
    # mistaken for it. Match by substring so 'force_n' / 'deflection_mm' work.
    _defl_tokens = ("deflection", "deflect", "displacement", "disp", "travel",
                    "stretch", "extension")
    _force_tokens = ("force", "load", "newton", "weight")
    for i, h in enumerate(normed):
        if d_idx is None and (h in _DEFL_KEYS or any(t in h for t in _defl_tokens)):
            d_idx = i
            if "mm" in h:
                defl_mm = True
    for i, h in enumerate(normed):
        if i == d_idx:
            continue
        if f_idx is None and (h in _FORCE_KEYS or any(t in h for t in _force_tokens)):
            f_idx = i
    return f_idx, d_idx, defl_mm


def spring_rate_from_bench_log(csv_bytes: bytes,
                               deflection_in_mm: Optional[bool] = None,
                               force_in_kgf: bool = False,
                               min_points: int = 3,
                               r2_floor: float = 0.98) -> BenchFit:
    """Fit a spring rate k from a measured bench log (CSV of force & deflection).

    This is the boundary-breaker: instead of a human reading a number off the
    bench and typing it in (and maybe mistyping it), the log itself becomes the
    input. k is the least-squares slope of force vs deflection — the honest,
    judge-friendly definition — and the fit's R² tells you whether the spring is
    actually linear over the range you tested.

    csv_bytes        : raw CSV. Needs a force column and a deflection column;
                       common header names are auto-detected (force/load/N,
                       deflection/travel/stretch/x, with a "_mm" hint for units).
    deflection_in_mm : force mm (True) or m (False); auto-detected from the header
                       if None, defaulting to mm (what teams record).
    force_in_kgf     : set True if the "force" column is really a hung MASS in kgf
                       (a stack of gym plates) — converted to N with g=9.80665.
    min_points       : refuse to trust a fit from fewer than this many points.
    r2_floor         : below this R² the spring isn't linear enough to trust one k.

    Returns a BenchFit. `is_trustworthy` is True only if there are enough points
    and the linear fit is good; otherwise the findings say exactly why not, and
    you should NOT feed the number forward as if it were measured.
    """
    findings: list = []
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = csv_bytes.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        findings.append(Finding("throttle-bench-fit", Severity.MISSING,
                                "Could not decode the bench log as text/CSV.",
                                subsystems=["brakes"]))
        return BenchFit(0.0, 0.0, 0.0, 0, False, findings)

    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        findings.append(Finding("throttle-bench-fit", Severity.MISSING,
                                "Bench log is empty.", subsystems=["brakes"]))
        return BenchFit(0.0, 0.0, 0.0, 0, False, findings)

    # Header row if the first row isn't all-numeric
    def _is_num(s):
        try:
            float(s)
            return True
        except Exception:
            return False

    header = None
    data_start = 0
    if not all(_is_num(c) for c in rows[0] if c.strip()):
        header = rows[0]
        data_start = 1

    f_idx = d_idx = None
    hdr_mm = False
    if header is not None:
        f_idx, d_idx, hdr_mm = _pick_columns(header)
    # Fallback: assume first two columns are (deflection, force) or (force, defl)?
    # Teams log both orders; if we couldn't name them, assume col0=deflection,
    # col1=force ONLY if there are exactly 2 columns, and say so.
    assumed_order = False
    if f_idx is None or d_idx is None:
        ncol = max(len(r) for r in rows[data_start:]) if rows[data_start:] else 0
        if ncol >= 2:
            d_idx, f_idx = 0, 1
            assumed_order = True
        else:
            findings.append(Finding(
                "throttle-bench-fit", Severity.MISSING,
                "Bench log needs a force column and a deflection column; couldn't "
                "find them. Add headers like 'force_N' and 'deflection_mm'.",
                subsystems=["brakes"]))
            return BenchFit(0.0, 0.0, 0.0, 0, False, findings)

    if deflection_in_mm is None:
        deflection_in_mm = hdr_mm if header is not None else True

    xs, ys = [], []
    for r in rows[data_start:]:
        if len(r) <= max(f_idx, d_idx):
            continue
        try:
            fval = float(r[f_idx])
            dval = float(r[d_idx])
        except Exception:
            continue
        if force_in_kgf:
            fval *= 9.80665
        if deflection_in_mm:
            dval /= 1000.0          # mm -> m
        xs.append(dval)
        ys.append(fval)

    n = len(xs)
    if n < min_points:
        findings.append(Finding(
            "throttle-bench-fit", Severity.WARN,
            f"Only {n} usable point(s) in the bench log — need >= {min_points} to "
            f"trust a fitted rate. Take more (hang several known loads, record the "
            f"deflection at each).", subsystems=["brakes"]))
        # still return a slope if 2 points, but not trustworthy
    # least-squares slope + intercept
    if n >= 2:
        mx = sum(xs) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxx <= 0:
            findings.append(Finding(
                "throttle-bench-fit", Severity.MISSING,
                "All deflection values are identical — can't fit a rate. Vary the "
                "load.", subsystems=["brakes"]))
            return BenchFit(0.0, 0.0, 0.0, n, False, findings)
        k = sxy / sxx
        b = my - k * mx
        # R^2
        ss_tot = sum((y - my) ** 2 for y in ys)
        ss_res = sum((y - (k * x + b)) ** 2 for x, y in zip(xs, ys))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    else:
        # single point: slope from origin, no R^2 meaning
        k = ys[0] / xs[0] if xs[0] != 0 else 0.0
        b = 0.0
        r2 = 0.0

    trustworthy = (n >= min_points) and (r2 >= r2_floor) and (k > 0)

    if k <= 0:
        findings.append(Finding(
            "throttle-bench-fit", Severity.FAIL,
            "Fitted a non-positive spring rate — the log's force/deflection sign or "
            "column mapping is wrong. Check the columns.", subsystems=["brakes"]))
    elif trustworthy:
        findings.append(Finding(
            "throttle-bench-fit", Severity.OK,
            f"Spring rate {k:.0f} N/m ({k/1000:.2f} N/mm) fitted from {n} measured "
            f"points, R²={r2:.4f}. This is the real part's rate — feed it straight "
            f"into the redundancy check; no retyping."
            + (" (Assumed column order deflection,force — confirm.)"
               if assumed_order else ""),
            subsystems=["brakes"],
            detail=dict(k_N_per_m=k, r_squared=r2, n_points=n)))
    else:
        findings.append(Finding(
            "throttle-bench-fit", Severity.WARN,
            f"Fitted {k:.0f} N/m but R²={r2:.4f} over {n} points is below the "
            f"{r2_floor:.2f} linearity floor — the spring isn't linear over this "
            f"range, so a single k is a poor description. Test over the ACTUAL "
            f"working travel, or model it as nonlinear.", subsystems=["brakes"],
            detail=dict(k_N_per_m=k, r_squared=r2, n_points=n)))

    return BenchFit(k_N_per_m=k, preload_N=b, r_squared=r2, n_points=n,
                    is_trustworthy=trustworthy, findings=findings)


# --------------------------------------------------------------------------- #
#  2) Cross-check the TYPED pedal dimensions against the real CAD envelope
# --------------------------------------------------------------------------- #
@dataclass
class CadCrossCheck:
    """Result of validating typed pedal dims against the CAD model's envelope."""
    ok: bool                       # dims are consistent with the CAD (or no basis)
    cad_units: Optional[str]
    cad_max_coord_mm: Optional[float]
    findings: list = field(default_factory=list)

    def as_dict(self):
        return dict(ok=self.ok, cad_units=self.cad_units,
                    cad_max_coord_mm=self.cad_max_coord_mm,
                    findings=[f.as_dict() for f in self.findings])


def _to_mm(value: float, units: Optional[str]) -> Optional[float]:
    """Convert a CAD coordinate to mm using the IGES unit string."""
    if value is None:
        return None
    u = (units or "").strip().lower()
    if u in ("mm", "millimeter", "millimetre", "millimeters", "millimetres"):
        return value
    if u in ("m", "meter", "metre", "meters", "metres"):
        return value * 1000.0
    if u in ("cm", "centimeter", "centimetre"):
        return value * 10.0
    if u in ("in", "inch", "inches"):
        return value * 25.4
    if u in ("ft", "foot", "feet"):
        return value * 304.8
    return None       # unknown unit -> can't convert, say so


def crosscheck_pedal_against_cad(width_mm: float, thickness_mm: float,
                                 lever_arm_mm: float,
                                 ingest_manifest: dict) -> CadCrossCheck:
    """Validate typed pedal dimensions against a real CAD upload's envelope.

    This breaks the transcription boundary from the other side: it can't read the
    pedal's thickness out of an IGES header (that geometry isn't there), but it CAN
    check the numbers you typed against what the CAD says the part physically is —
    its overall size and its units. That catches the errors that actually happen:
    a lever arm typed in inches, a width off by 10×, or a dimension bigger than the
    whole model.

    ingest_manifest : the dict from cad_ingest.ingest_bundle() (its "provenance"
                      carries IGES units + max_coord).

    Returns a CadCrossCheck. `ok` is True if the dims are consistent with the CAD
    OR there's no CAD basis to check against (in which case it says so — absence of
    a check is reported, never silently treated as a pass).
    """
    findings: list = []
    prov = (ingest_manifest or {}).get("provenance", {}) or {}
    units = prov.get("units")
    max_coord_raw = prov.get("max_coord")

    max_coord_mm = None
    if max_coord_raw is not None:
        try:
            max_coord_mm = _to_mm(float(max_coord_raw), units)
        except Exception:
            max_coord_mm = None

    if max_coord_mm is None:
        findings.append(Finding(
            "throttle-pedal-cad", Severity.MISSING,
            "No usable CAD envelope in the upload (IGES max-coordinate/units not "
            "found or unit unknown), so the typed pedal dimensions can't be "
            "cross-checked against the model. The dims stand on your entry alone.",
            subsystems=["brakes"],
            detail=dict(units=units, max_coord=max_coord_raw)))
        return CadCrossCheck(ok=True, cad_units=units,
                             cad_max_coord_mm=None, findings=findings)

    biggest_typed = max(abs(width_mm), abs(thickness_mm), abs(lever_arm_mm))
    # The largest single typed dimension should not exceed the model's overall
    # envelope (with a little slack for the coordinate being a radius-from-origin).
    envelope = max_coord_mm * 2.0
    ok = True
    if biggest_typed > envelope * 1.05:
        ok = False
        findings.append(Finding(
            "throttle-pedal-cad", Severity.FAIL,
            f"Typed pedal dimension {biggest_typed:.0f} mm EXCEEDS the CAD model's "
            f"overall envelope (~{envelope:.0f} mm from the IGES bounds, units "
            f"'{units}'). That's a transcription error — likely wrong units (inches "
            f"vs mm) or an extra digit. Re-check the number against the model before "
            f"trusting any pedal verdict.", subsystems=["brakes"],
            detail=dict(biggest_typed_mm=biggest_typed, cad_envelope_mm=envelope,
                        cad_units=units)))
    elif biggest_typed < envelope * 0.02:
        # typed dims are tiny vs a big model — possibly the pedal is one part of a
        # larger assembly export; warn, don't fail.
        findings.append(Finding(
            "throttle-pedal-cad", Severity.WARN,
            f"Typed pedal dimensions are small (max {biggest_typed:.0f} mm) versus a "
            f"CAD envelope of ~{envelope:.0f} mm — fine if this IGES is a whole "
            f"assembly, but confirm you exported the pedal, not the pedal box.",
            subsystems=["brakes"],
            detail=dict(biggest_typed_mm=biggest_typed, cad_envelope_mm=envelope)))
    else:
        findings.append(Finding(
            "throttle-pedal-cad", Severity.OK,
            f"Typed pedal dimensions are consistent with the CAD envelope "
            f"(~{envelope:.0f} mm, units '{units}'). Transcription sanity-checked "
            f"against the model.", subsystems=["brakes"],
            detail=dict(biggest_typed_mm=biggest_typed, cad_envelope_mm=envelope)))

    if units and units.lower() not in ("mm", "millimeter", "millimetre"):
        findings.append(Finding(
            "throttle-pedal-cad", Severity.WARN,
            f"The CAD model's units are '{units}', but the pedal view expects mm. "
            f"Make sure the dimensions you typed are in mm, not '{units}'.",
            subsystems=["brakes"], detail=dict(cad_units=units)))

    return CadCrossCheck(ok=ok, cad_units=units, cad_max_coord_mm=max_coord_mm,
                         findings=findings)
