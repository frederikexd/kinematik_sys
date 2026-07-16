# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
hardpoint_import.py — bring hardpoints in from OptimumK exports / Excel / CSV
==============================================================================

The switching cost that keeps a team on their old workflow is re-typing
hardpoints. This module removes it: upload the spreadsheet you already have
(an OptimumK point export, a team Excel sheet, a CSV from anywhere) and get a
KinematiK hardpoint set with every assumption made EXPLICIT.

Honesty contract (this is the product's brand, keep it):
  * A point name that could mean two targets is reported AMBIGUOUS and left
    unmapped — never coin-flipped.
  * Units are taken from headers when present; otherwise inferred from
    magnitudes and REPORTED with the basis for the guess, for the user to
    override before applying.
  * Axis convention is the user's explicit choice (frame charter blurbs in
    coordinate_frames.py explain each); the conversion applied is reported.
  * Side mirroring and re-origining are applied only when detected/requested
    and always itemised in the report.

Pipeline:  parse_tabular() -> group_corners() -> map_names() -> build_result()
Everything is pure and importable without Streamlit; openpyxl loads lazily.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "RawPoint", "MappedPoint", "ImportResult",
    "parse_tabular", "group_corners", "map_names", "build_result",
    "CANONICAL_KEYS", "infer_units",
]


# --------------------------------------------------------------------------- #
#  Data shapes
# --------------------------------------------------------------------------- #
@dataclass
class RawPoint:
    name: str
    x: float
    y: float
    z: float
    sheet: str = ""
    row: int = 0
    corner: str = ""       # "", "FL", "FR", "RL", "RR", "F", "R", "L", "Rt"

    @property
    def coords(self):
        return (self.x, self.y, self.z)


@dataclass
class MappedPoint:
    key: str               # canonical KinematiK hardpoint key
    raw: RawPoint
    xyz_mm: tuple          # final coords: mm, kinematik frame, after all steps


@dataclass
class ImportResult:
    mapped: dict = field(default_factory=dict)        # key -> [x, y, z] (mm)
    details: list = field(default_factory=list)       # list[MappedPoint]
    unmapped: list = field(default_factory=list)      # list[RawPoint]
    ambiguous: list = field(default_factory=list)     # list[(RawPoint, [keys])]
    unit: str = "mm"
    unit_basis: str = ""
    frame_key: str = "iso8855"
    corner: str = ""
    mirrored: bool = False
    reorigined: bool = False
    reorigin_shift: tuple = (0.0, 0.0, 0.0)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.mapped)

    def summary(self) -> str:
        parts = [f"{len(self.mapped)} points mapped"]
        if self.unmapped:
            parts.append(f"{len(self.unmapped)} unrecognised")
        if self.ambiguous:
            parts.append(f"{len(self.ambiguous)} ambiguous (left unmapped)")
        parts.append(f"units: {self.unit} ({self.unit_basis})")
        parts.append(f"frame: {self.frame_key}")
        if self.mirrored:
            parts.append("mirrored L→R")
        if self.reorigined:
            dx, dy, dz = self.reorigin_shift
            parts.append(f"re-origined (Δx={dx:.1f}, Δz={dz:.1f} mm)")
        return "; ".join(parts)


# --------------------------------------------------------------------------- #
#  1) Parse — find point tables in CSV / XLSX, tolerant of layout
# --------------------------------------------------------------------------- #
_HDR_NAME = re.compile(r"^\s*(point|name|label|description|hardpoint|pt)\b", re.I)
_HDR_X = re.compile(r"^\s*x\b|\blong", re.I)
_HDR_Y = re.compile(r"^\s*y\b|\blat", re.I)
_HDR_Z = re.compile(r"^\s*z\b|\bvert", re.I)
_UNIT_IN_HDR = re.compile(r"\(([^)]*)\)|\[([^\]]*)\]")


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def _unit_from_header(cells) -> Optional[str]:
    for c in cells:
        for m in _UNIT_IN_HDR.finditer(str(c or "")):
            u = (m.group(1) or m.group(2) or "").strip().lower()
            if u in ("mm", "millimeter", "millimetre", "millimeters", "millimetres"):
                return "mm"
            if u in ("m", "meter", "metre", "meters", "metres"):
                return "m"
            if u in ("in", "inch", "inches", '"'):
                return "in"
    return None


def _rows_from_csv(data: bytes) -> list[tuple[str, list]]:
    text = data.decode("utf-8-sig", errors="replace")
    # deterministic delimiter choice: whichever of , ; tab appears most in the
    # first lines wins (the Sniffer fails on headerless one-column-ish files;
    # OptimumK exports and euro-locale sheets commonly use ;)
    head = "\n".join(text.splitlines()[:10])
    delim = max(",;\t", key=head.count) if any(d in head for d in ",;\t") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return [("csv", r) for r in rows]


def _rows_from_xlsx(data: bytes) -> list[tuple[str, list]]:
    import openpyxl                              # lazy: heavy import
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            out.append((ws.title, list(row)))
    return out


def parse_tabular(data: bytes, filename: str = "") -> tuple[list[RawPoint], Optional[str]]:
    """Extract (points, header_unit_hint) from a CSV or XLSX byte blob.

    Layout tolerance: finds a header row (name + X/Y/Z columns, in any order,
    with junk columns between) anywhere in any sheet; below a header, rows are
    read positionally. Without any header, rows shaped [text, num, num, num]
    are accepted as points. Blank rows end a table; several tables per sheet
    are fine."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xltx")):
        rows = _rows_from_xlsx(data)
    elif name.endswith(".csv") or not name:
        rows = _rows_from_csv(data)
    else:
        # try both — xlsx magic is a zip ("PK")
        rows = _rows_from_xlsx(data) if data[:2] == b"PK" else _rows_from_csv(data)

    points: list[RawPoint] = []
    unit_hint: Optional[str] = None
    cols: Optional[dict] = None                  # {name: i, x: i, y: i, z: i}
    cur_sheet = None

    for rix, (sheet, row) in enumerate(rows):
        if sheet != cur_sheet:
            cur_sheet, cols = sheet, None        # headers don't cross sheets
        cells = ["" if c is None else c for c in row]
        if not any(str(c).strip() for c in cells):
            cols = None                          # blank row ends the table
            continue

        # header row?
        idx = {"name": None, "x": None, "y": None, "z": None}
        for i, c in enumerate(cells):
            s = str(c)
            if idx["name"] is None and _HDR_NAME.search(s):
                idx["name"] = i
            elif idx["x"] is None and _HDR_X.search(s) and _num(c) is None:
                idx["x"] = i
            elif idx["y"] is None and _HDR_Y.search(s) and _num(c) is None:
                idx["y"] = i
            elif idx["z"] is None and _HDR_Z.search(s) and _num(c) is None:
                idx["z"] = i
        if idx["x"] is not None and idx["y"] is not None and idx["z"] is not None:
            if idx["name"] is None:              # name = first non-XYZ text col
                for i, c in enumerate(cells):
                    if i not in (idx["x"], idx["y"], idx["z"]) and str(c).strip():
                        idx["name"] = i
                        break
            if idx["name"] is not None:
                cols = idx
                unit_hint = unit_hint or _unit_from_header(cells)
                continue

        # data row under a known header
        if cols is not None:
            try:
                nm = str(cells[cols["name"]]).strip()
                xyz = [_num(cells[cols[a]]) for a in ("x", "y", "z")]
            except IndexError:
                continue
            if nm and all(v is not None for v in xyz):
                points.append(RawPoint(nm, *xyz, sheet=sheet, row=rix))
            continue

        # headerless fallback: [text, num, num, num] anywhere in the row
        text_cell, nums = None, []
        for c in cells:
            v = _num(c)
            if v is not None:
                nums.append(v)
            elif str(c).strip() and text_cell is None:
                text_cell = str(c).strip()
        if text_cell and len(nums) >= 3:
            points.append(RawPoint(text_cell, nums[0], nums[1], nums[2],
                                   sheet=sheet, row=rix))
    return points, unit_hint


# --------------------------------------------------------------------------- #
#  2) Corners — detect FL/FR/RL/RR / front / left labels in names
# --------------------------------------------------------------------------- #
_CORNER_PATTERNS = [
    ("FL", re.compile(r"\bfl\b|front[\s_-]*left|\blf\b|left[\s_-]*front", re.I)),
    ("FR", re.compile(r"\bfr\b|front[\s_-]*right|\brf\b|right[\s_-]*front", re.I)),
    ("RL", re.compile(r"\brl\b|rear[\s_-]*left|\blr\b|left[\s_-]*rear", re.I)),
    ("RR", re.compile(r"\brr\b|rear[\s_-]*right|right[\s_-]*rear", re.I)),
    ("F",  re.compile(r"\bfront\b|\bfrt\b|\bfwd\b", re.I)),
    ("R",  re.compile(r"\brear\b|\baft\b", re.I)),
    ("L",  re.compile(r"\bleft\b|\blh\b", re.I)),
    ("Rt", re.compile(r"\bright\b|\brh\b", re.I)),
]


def group_corners(points: list[RawPoint]) -> dict[str, list[RawPoint]]:
    """Tag each point's corner from its name and bucket them. Points with no
    corner label land in "" and are offered with every corner (single-corner
    sheets usually have no labels at all)."""
    out: dict[str, list[RawPoint]] = {}
    for p in points:
        tag = ""
        for t, pat in _CORNER_PATTERNS:
            if pat.search(p.name):
                tag = t
                break
        p.corner = tag
        out.setdefault(tag, []).append(p)
    return out


def points_for_corner(groups: dict, corner: str) -> list[RawPoint]:
    """The working set for one corner: its own points plus the unlabeled ones.
    'FL' also picks up 'F' and 'L' buckets, etc."""
    take = {"", corner}
    if len(corner) == 2:
        take |= {corner[0], "L" if corner[1] == "L" else "Rt"}
    return [p for tag, pts in groups.items() if tag in take for p in pts]


# --------------------------------------------------------------------------- #
#  3) Name mapping — concept groups, ambiguity refused
# --------------------------------------------------------------------------- #
# Concept vocabularies (token level, after normalisation)
_C = {
    "upper":  {"upper", "top", "uca", "uwb", "ucaarm"},
    "lower":  {"lower", "bottom", "lca", "lwb", "lcaarm"},
    "front":  {"front", "fore", "fwd", "forward", "leading"},
    "rear":   {"rear", "aft", "rearward", "trailing", "back"},
    "inner":  {"inner", "inboard", "chassis", "pivot", "frame", "in"},
    "outer":  {"outer", "outboard", "upright", "balljoint", "bj", "knuckle",
               "out"},
    "tierod": {"tie", "tierod", "steering", "track", "trackrod", "toe",
               "toelink", "steer"},
    "wheel":  {"wheel", "wc", "wheelcenter", "wheelcentre"},
    "center": {"center", "centre", "ctr"},
    "patch":  {"contact", "patch", "cp", "tire", "tyre", "ground"},
    "pushrod": {"pushrod", "push", "prod", "pullrod", "pull"},
    "rocker": {"rocker", "bellcrank", "bell", "crank"},
    "spring": {"spring", "damper", "shock", "coilover", "coil"},
    "pivot":  {"pivot", "axis"},
}


def _tokens(name: str) -> set:
    s = re.sub(r"[^a-z0-9]+", " ", name.lower())
    toks = set(s.split())
    joined = s.replace(" ", "")
    # catch fused forms: "tierod", "wheelcenter", "bellcrank", "pushrod"
    for fused in ("tierod", "trackrod", "toelink", "wheelcenter", "wheelcentre",
                  "bellcrank", "pushrod", "pullrod", "balljoint", "contactpatch"):
        if fused in joined:
            toks.add(fused)
    return toks


def _has(toks: set, concept: str) -> bool:
    return bool(toks & _C[concept])


# Each canonical key: (required concepts, forbidden concepts)
# A raw name maps to a key iff it hits ALL required and NO forbidden concepts.
CANONICAL_KEYS: dict[str, tuple[tuple, tuple]] = {
    "upper_front_inner": (("upper", "front", "inner"), ("tierod", "pushrod", "rocker", "spring", "outer")),
    "upper_rear_inner":  (("upper", "rear", "inner"),  ("tierod", "pushrod", "rocker", "spring", "outer")),
    "lower_front_inner": (("lower", "front", "inner"), ("tierod", "pushrod", "rocker", "spring", "outer")),
    "lower_rear_inner":  (("lower", "rear", "inner"),  ("tierod", "pushrod", "rocker", "spring", "outer")),
    "upper_outer":       (("upper", "outer"),          ("tierod", "pushrod", "rocker", "spring", "inner")),
    "lower_outer":       (("lower", "outer"),          ("tierod", "pushrod", "rocker", "spring", "inner")),
    "tie_rod_inner":     (("tierod", "inner"),         ("pushrod", "rocker", "outer")),
    "tie_rod_outer":     (("tierod", "outer"),         ("pushrod", "rocker")),
    "wheel_center":      (("wheel", "center"),         ("patch",)),
    "contact_patch":     (("patch",),                  ("wheel",)),
    "pushrod_outer":     (("pushrod", "outer"),        ("rocker",)),
    "rocker_pushrod":    (("rocker", "pushrod"),       ()),
    "rocker_pivot":      (("rocker", "pivot"),         ("pushrod", "spring")),
    "rocker_spring":     (("rocker", "spring"),        ("pushrod", "inner")),
    "spring_inner":      (("spring", "inner"),         ("rocker", "outer")),
}


def map_names(points: list[RawPoint]
              ) -> tuple[dict[str, RawPoint], list[RawPoint], list[tuple]]:
    """Return (mapped {key: point}, unmapped, ambiguous [(point, keys)]).

    Ambiguity is refused twice over: a NAME matching several keys is reported,
    and two names claiming the SAME key are both reported rather than one
    silently winning."""
    mapped: dict[str, RawPoint] = {}
    claims: dict[str, list[RawPoint]] = {}
    unmapped: list[RawPoint] = []
    ambiguous: list[tuple] = []

    for p in points:
        toks = _tokens(p.name)
        hits = [k for k, (req, forb) in CANONICAL_KEYS.items()
                if all(_has(toks, c) for c in req)
                and not any(_has(toks, c) for c in forb)]
        # wheel_center special-case: bare "wheel" with no other concept
        if not hits and _has(toks, "wheel") and not (_has(toks, "patch")):
            hits = ["wheel_center"]
        if len(hits) == 1:
            claims.setdefault(hits[0], []).append(p)
        elif len(hits) > 1:
            ambiguous.append((p, hits))
        else:
            unmapped.append(p)

    for key, claimants in claims.items():
        if len(claimants) == 1:
            mapped[key] = claimants[0]
        else:
            for p in claimants:
                ambiguous.append((p, [key]))
    return mapped, unmapped, ambiguous


# --------------------------------------------------------------------------- #
#  4) Units
# --------------------------------------------------------------------------- #
_TO_MM = {"mm": 1.0, "m": 1000.0, "in": 25.4}


def infer_units(points: list[RawPoint], header_hint: Optional[str] = None
                ) -> tuple[str, str]:
    """Return (unit, basis). Header hint wins; else magnitude heuristic on the
    spread of coordinates (an FSAE corner spans ~0.6 m / ~600 mm / ~24 in)."""
    if header_hint in _TO_MM:
        return header_hint, "declared in the file's column headers"
    vals = [abs(v) for p in points for v in p.coords if v]
    if not vals:
        return "mm", "no numeric data — defaulted"
    big = sorted(vals)[int(len(vals) * 0.9)] if len(vals) > 2 else max(vals)
    if big < 3.0:
        return "m", f"largest coordinates ≈ {big:.2f} — metres is the only unit that puts a corner at car scale"
    if big < 60.0:
        return "in", f"largest coordinates ≈ {big:.0f} — consistent with inches (a corner spans ~24 in)"
    return "mm", f"largest coordinates ≈ {big:.0f} — consistent with millimetres"


# --------------------------------------------------------------------------- #
#  5) Assemble — units → frame → mirror → re-origin, all reported
# --------------------------------------------------------------------------- #
def build_result(points: list[RawPoint], *,
                 frame_key: str = "iso8855",
                 unit: Optional[str] = None,
                 header_hint: Optional[str] = None,
                 corner: str = "",
                 mirror: Optional[bool] = None,
                 reorigin: bool = True) -> ImportResult:
    """Run the full pipeline on an already-corner-filtered point list.

    frame_key: a key of coordinate_frames.BUILTIN_FRAMES describing the
               SOURCE file's axes (user-chosen in the UI).
    unit:      override; None = infer (header hint, then magnitudes).
    mirror:    None = auto (mirror when median y < 0 after conversion, i.e.
               a left-side corner arriving in a y-right frame).
    reorigin:  shift so wheel-centre x → 0 and ground z → 0, KinematiK's
               editor convention.
    """
    res = ImportResult(frame_key=frame_key, corner=corner)
    mapped_pts, res.unmapped, res.ambiguous = map_names(points)
    if not mapped_pts:
        res.warnings.append("No recognisable hardpoint names found — check "
                            "the file has a name column and X/Y/Z columns.")
        return res

    res.unit, res.unit_basis = (unit, "set by user") if unit in _TO_MM \
        else infer_units(list(mapped_pts.values()), header_hint)
    scale = _TO_MM[res.unit]

    # frame rotation (pure rotation: origins handled by the re-origin step)
    import coordinate_frames as _cf
    src = _cf.BUILTIN_FRAMES[frame_key]
    dst = _cf.BUILTIN_FRAMES["kinematik"]
    zero = (0.0, 0.0, 0.0)

    conv: dict[str, list] = {}
    for key, p in mapped_pts.items():
        v = [c * scale for c in p.coords]
        q = _cf.convert_point(v, src, dst, zero, zero)
        conv[key] = [float(q[0]), float(q[1]), float(q[2])]

    # mirror: KinematiK's editor corner lives at +y; a left-side corner
    # arrives with y < 0 after conversion
    ys = sorted(v[1] for v in conv.values())
    median_y = ys[len(ys) // 2]
    do_mirror = (median_y < 0) if mirror is None else bool(mirror)
    if do_mirror:
        for v in conv.values():
            v[1] = -v[1]
        res.mirrored = True
        if mirror is None:
            res.warnings.append("Points arrived on the left side (y < 0) — "
                                "mirrored to KinematiK's +y corner. Untick "
                                "'mirror' if that's wrong.")

    # re-origin to the editor convention: x = 0 at wheel centre, z = 0 at
    # the contact patch (or the lowest point when no patch is present)
    if reorigin:
        dx = conv.get("wheel_center", [0, 0, 0])[0] if "wheel_center" in conv else 0.0
        if "contact_patch" in conv:
            dz = conv["contact_patch"][2]
        else:
            dz = min(v[2] for v in conv.values())
            if "wheel_center" in conv:      # better estimate: WC z - tyre radius unknown → keep min
                pass
        if abs(dx) > 1e-9 or abs(dz) > 1e-9:
            for v in conv.values():
                v[0] -= dx
                v[2] -= dz
            res.reorigined = True
            res.reorigin_shift = (dx, 0.0, dz)

    res.mapped = conv
    res.details = [MappedPoint(k, mapped_pts[k], tuple(conv[k]))
                   for k in conv]

    missing_core = [k for k in ("upper_front_inner", "upper_rear_inner",
                                "lower_front_inner", "lower_rear_inner",
                                "upper_outer", "lower_outer",
                                "tie_rod_inner", "tie_rod_outer",
                                "wheel_center", "contact_patch")
                    if k not in conv]
    if missing_core:
        res.warnings.append(
            "Missing for a complete double-wishbone corner: "
            + ", ".join(missing_core)
            + " — these keep their current editor values.")
    return res
