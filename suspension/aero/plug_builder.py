# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Plug & layup build planner — the shop-floor half of a scaled aero build.

`scale_model.py` answers "will the coefficient measured on the scaled part even
transfer?" (similitude, tolerance, mount alignment). This module answers the
question that comes chronologically FIRST and that every aero meeting actually
spends its time on: "how do we physically make the thing, what do we order by
Friday, and does the whole build fit in one Saturday?"

WHY THIS MODULE EXISTS (read an aero meeting's minutes and count the holes)
---------------------------------------------------------------------------
A real nosecone-plug meeting reads like this, near-verbatim:

    "Please check the needed supplies asap since we want to order for next week"
    "Slice the scaled body into horizontal layers — each layer's outline becomes
     the cutting template ... double-check tolerances after scaling since small
     dimension errors compound as the layers stack"
    "Build up [coating] thickness carefully so the structural carbon layers
     underneath are never sanded into"
    "Everything happens in one build day — plan buffer time between
     adhesive/resin cure steps ... assign roles ahead of time"

Every one of those lines is a computable engineering statement, and today none
of them is computed anywhere. They live as prose on a slide and a disconnected
Google Sheet, which is precisely the class of error KinematiK exists to kill:

  1. THE SLICE PLAN.  "Slice into horizontal layers" hides real arithmetic: how
     many sheets of NGX foam tall is a 260 mm nosecone once every glue line adds
     its bondline? Which outline does each layer get cut to (the slab's larger
     face — you sand DOWN to the loft, you can't sand material back on)? A layer
     count done by eye is how a stack comes out 8 mm short and the "1:2.5" model
     quietly becomes 1:2.58.

  2. STACK-UP TOLERANCE.  "Errors compound as the layers stack" — quantified,
     not just feared. Every sheet carries a thickness tolerance and every glue
     line a bondline tolerance; ten layers of ±0.5 mm foam and nine ±0.2 mm
     bondlines are a ±1.7 mm (RSS) / ±6.8 mm (worst-case) height uncertainty.
     `StackTolerance` computes both and hands them straight to
     `scale_model.ToleranceBudget`, so the build method's own error shows up in
     the SAME coefficient-uncertainty band the correlation report reads.

  3. TEMPLATES.  "Print or trace templates onto the foam" fails silently when a
     printer rescales to 97%. Every template this module emits is a 1:1-mm SVG
     with a printed 100 mm verification bar — measure the bar, trust the sheet —
     plus the layer's sand-to line (its top outline) drawn dashed inside the cut
     line, so the shaping crew knows where the loft IS instead of eyeballing it.

  4. THE BILL OF MATERIALS.  "Check the needed supplies" becomes a computed,
     margin-carrying list: foam sheets from actual layer nesting, adhesive from
     bonded area, fabric/resin from the lofted shell area and ply schedule,
     coating resin from build thickness, consumables and PPE from crew size.
     Exportable as CSV so it drops into the team's ordering sheet. Every number
     is labelled an ESTIMATE with its basis — it sizes an order, not a force.

  5. THE BUILD DAY.  "One build day with buffer between cure steps" is a
     scheduling problem with passive edges (a cure occupies the clock but frees
     the crew). `BuildDaySchedule` runs the slide's own plan through a
     dependency-and-crew scheduler and answers honestly: the two-stage layup
     with realistic epoxy cures usually does NOT fit a single day — better to
     learn that on Tuesday than at 21:40 on build Saturday. It names the
     critical path and the standard fixes (glue the stack the night before;
     accept a demold-next-morning finish).

  6. HARD GATES.  The two rules that scrap a part when skipped become literal
     preflight checks: the release barrier MUST precede any layup (or the foam
     never comes out of the shell), and the coating build thickness MUST exceed
     the planned flatting depth plus margin (or the DA sander eats structural
     carbon). Same philosophy as the manufacturing-release gate: a go/no-go, in
     software, before the resin is mixed.

WHAT THIS MODULE OWNS (and what it deliberately does not)
---------------------------------------------------------
  * `NoseconeBody`     — a parametric scaled nosecone loft (power-law planform +
                         height profile, superellipse sections) good enough to
                         slice, template, and area-take-off. NOT a CFD surface.
  * `FoamSheet`        — the stock the shop actually has, with its tolerances.
  * `SlicePlan`        — layer count, per-layer z-band and outlines, nesting
                         area, and the stack-up `StackTolerance` (RSS + worst
                         case) with a one-call handoff into `ToleranceBudget`.
  * `layer_template_svg` — 1:1 printable cutting template per layer (cut line,
                         dashed sand-to line, centreline datum, scale-check bar).
  * `LayupRecipe` + `MaterialsEstimate` — ply schedule and process assumptions
                         -> quantified BOM with margins, CSV export.
  * `BuildStep` / `default_build_day` / `BuildDaySchedule` — the slide-13 plan
                         as data, scheduled against crews and cure times, with a
                         fits-in-the-day verdict and the critical path named.
  * `PreflightGate`    — the release-barrier and don't-sand-into-carbon gates,
                         plus stack, sliver-layer and similitude advisories.
  * `PlugBuildPlan`    — one object tying all of it together with a provenance
                         string, mirroring `ScaledRunPlan` on the tunnel side.

DELIBERATE NON-GOALS, same discipline as the rest of `suspension.aero`: this
module does not produce an aero coefficient, does not claim the loft is the CAD
surface (import real sections via `NoseconeBody.from_sections` when you have
them), and every g/mL/minute it emits is a stated engineering estimate whose
basis rides along in the output. A gap it reports (stack too short, cure past
midnight, coating thinner than the sanding depth) is a real gap.

Quick start (runnable today — the meeting's own 1:2.5 nosecone):
    from suspension.aero import (NoseconeBody, FoamSheet, SlicePlan,
                                 LayupRecipe, MaterialsEstimate,
                                 default_build_day, BuildDaySchedule,
                                 PreflightGate, PlugBuildPlan)

    body  = NoseconeBody(length_mm=520, base_width_mm=250, base_height_mm=260)
    sheet = FoamSheet(thickness_mm=25.4, length_mm=1220, width_mm=610)
    plan  = SlicePlan.plan(body, sheet)
    print(plan.summary())                      # layers, glue lines, stack error

    recipe = LayupRecipe(plies=2)
    bom    = MaterialsEstimate.compute(body, plan, recipe, sheet, crew_size=6)
    print(bom.summary());  open("order.csv", "w").write(bom.to_csv())

    sched = BuildDaySchedule.plan(default_build_day(plan, recipe))
    print(sched.verdict)                       # does slide 13 survive contact?

    build = PlugBuildPlan(body, plan, recipe, bom, sched)
    print(build.report())
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence

from .scale_model import ScaleSpec, SimilitudePlan, ToleranceBudget


# --------------------------------------------------------------------------- #
#  Geometry — a sliceable, template-able, area-take-off-able nosecone loft
# --------------------------------------------------------------------------- #
#
# The loft is deliberately simple: plan half-width and crest height follow
# power laws of station (n ~ 0.5 blunt, 1.0 straight taper), and each vertical
# cross-section is a superellipse over a flat underside. That family covers the
# blunt-nosed, flat-floored FSAE nosecone well enough to (a) slice into layers,
# (b) print honest templates, and (c) take off shell area for the BOM — which is
# all the shop needs. It is NOT the CFD surface; when the team exports real
# sections from CAD, `from_sections` swaps them in and everything downstream
# (slicing, templates, BOM) uses the real outlines unchanged.
@dataclass(frozen=True)
class NoseconeBody:
    """
    The SCALED article, in shop millimetres (x=0 at the nose tip, x=length at
    the base; z=0 on the flat underside, z up).

    `nose_exponent`    — plan half-width w(x) = (W/2)·(x/L)^n. 0.5 ≈ blunt
                         elliptic nose, 1.0 = straight cone.
    `height_exponent`  — crest height h(x) = H·(x/L)^m, same reading.
    `section_exponent` — superellipse power e of each cross-section
                         (|y|/w)^e + (z/h)^e ≤ 1. 2 = elliptic, 2.5–3 =
                         squarer FSAE-typical sections.
    """
    length_mm: float
    base_width_mm: float
    base_height_mm: float
    nose_exponent: float = 0.6
    height_exponent: float = 0.75
    section_exponent: float = 2.5
    note: str = ""
    # optional CAD-section override: callable z_mm -> closed outline [(x,y)...]
    _outline_fn: Optional[Callable[[float], Sequence[tuple]]] = None

    def __post_init__(self):
        if min(self.length_mm, self.base_width_mm, self.base_height_mm) <= 0:
            raise ValueError("nosecone length/width/height must be positive")
        if not (0.2 <= self.nose_exponent <= 2.0):
            raise ValueError("nose_exponent outside a sane 0.2–2.0 range")
        if not (0.2 <= self.height_exponent <= 2.0):
            raise ValueError("height_exponent outside a sane 0.2–2.0 range")
        if self.section_exponent < 1.5:
            raise ValueError("section_exponent < 1.5 gives a diamond section — "
                             "almost certainly not the nosecone you meant")

    # -- the loft ----------------------------------------------------------- #
    def plan_half_width(self, x_mm: float) -> float:
        """Half-width of the planform at station x (0 at the tip)."""
        x = min(max(x_mm, 0.0), self.length_mm)
        return 0.5 * self.base_width_mm * (x / self.length_mm) ** self.nose_exponent

    def crest_height(self, x_mm: float) -> float:
        """Top-surface height above the flat underside at station x."""
        x = min(max(x_mm, 0.0), self.length_mm)
        return self.base_height_mm * (x / self.length_mm) ** self.height_exponent

    def half_width_at(self, x_mm: float, z_mm: float) -> float:
        """Half-width of the solid at station x and height z (0 if above crest)."""
        h = self.crest_height(x_mm)
        if h <= 0.0 or z_mm >= h:
            return 0.0
        if z_mm <= 0.0:
            return self.plan_half_width(x_mm)
        e = self.section_exponent
        return self.plan_half_width(x_mm) * (1.0 - (z_mm / h) ** e) ** (1.0 / e)

    def slice_outline(self, z_mm: float, n_pts: int = 60) -> list:
        """
        The closed plan-view outline of a horizontal cut at height z — exactly
        the polygon a foam-layer cutting template needs. Points run nose→base
        along +y then back along -y. Empty if z is at/above the crest everywhere.
        """
        if self._outline_fn is not None:
            return list(self._outline_fn(z_mm))
        if z_mm >= self.base_height_mm:
            return []
        # first station where the crest clears z (the layer's forward tip)
        if z_mm <= 0.0:
            x_tip = 0.0
        else:
            x_tip = self.length_mm * (z_mm / self.base_height_mm) ** (1.0 / self.height_exponent)
        if x_tip >= self.length_mm:
            return []
        xs = [x_tip + (self.length_mm - x_tip) * i / (n_pts - 1) for i in range(n_pts)]
        upper = [(x, self.half_width_at(x, z_mm)) for x in xs]
        lower = [(x, -y) for (x, y) in reversed(upper)]
        return upper + lower

    @staticmethod
    def from_sections(length_mm: float, base_width_mm: float, base_height_mm: float,
                      outline_fn: Callable[[float], Sequence[tuple]],
                      note: str = "CAD sections") -> "NoseconeBody":
        """
        Swap in real CAD-exported outlines: `outline_fn(z_mm)` returns the closed
        (x, y) outline at height z. Slicing, templates and BOM then run on the
        REAL geometry; the parametric loft is only the no-CAD-yet default.
        """
        return NoseconeBody(length_mm=length_mm, base_width_mm=base_width_mm,
                            base_height_mm=base_height_mm, note=note,
                            _outline_fn=outline_fn)

    # -- area / volume take-off (the BOM's inputs) --------------------------- #
    def shell_area_m2(self, include_underside: bool = True, n: int = 200) -> float:
        """
        Outer skin area of the loft, numerically. ESTIMATE for material take-off
        (fabric, peel ply, release film, coating), good to a few % on this loft
        family — it sizes an order, never a force. Underside is included by
        default because the layup plan laminates it (stage one of two).
        """
        dx = self.length_mm / n
        area_mm2 = 0.0
        prev_arc = self._section_arc_mm(0.0)
        prev_w = self.plan_half_width(0.0)
        for i in range(1, n + 1):
            x = i * dx
            arc = self._section_arc_mm(x)
            w = self.plan_half_width(x)
            # streamwise slope correction from the planform taper (order-correct)
            ds = math.hypot(dx, w - prev_w)
            area_mm2 += 0.5 * (arc + prev_arc) * ds
            if include_underside:
                area_mm2 += (w + prev_w) * dx          # flat floor strip, width 2w avg
            prev_arc, prev_w = arc, w
        return area_mm2 / 1.0e6

    def volume_l(self, n: int = 200) -> float:
        """Solid volume of the plug in litres (foam quantity sanity check)."""
        dx = self.length_mm / n
        vol_mm3 = 0.0
        for i in range(n):
            x = (i + 0.5) * dx
            vol_mm3 += self._section_area_mm2(x) * dx
        return vol_mm3 / 1.0e6

    def _section_arc_mm(self, x_mm: float, k: int = 48) -> float:
        """Arc length of the superellipse top surface of the section at x."""
        w, h = self.plan_half_width(x_mm), self.crest_height(x_mm)
        if w <= 0 or h <= 0:
            return 0.0
        e = self.section_exponent
        pts = []
        for i in range(k + 1):
            y = -w + 2.0 * w * i / k
            z = h * (1.0 - (abs(y) / w) ** e) ** (1.0 / e) if abs(y) < w else 0.0
            pts.append((y, z))
        return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                   for i in range(k))

    def _section_area_mm2(self, x_mm: float, k: int = 48) -> float:
        w, h = self.plan_half_width(x_mm), self.crest_height(x_mm)
        if w <= 0 or h <= 0:
            return 0.0
        e = self.section_exponent
        dz = h / k
        return sum(2.0 * self.half_width_at(x_mm, (j + 0.5) * dz) * dz for j in range(k))

    def label(self) -> str:
        return (f"nosecone loft L{self.length_mm:g} × W{self.base_width_mm:g} × "
                f"H{self.base_height_mm:g} mm"
                + (f" ({self.note})" if self.note else ""))


# --------------------------------------------------------------------------- #
#  The foam stock and the slice plan — "each layer's outline becomes the template"
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FoamSheet:
    """
    The stock the shop actually has — dimensions AND tolerances, because the
    tolerances are what compound up the stack. Defaults are a common 1" NGX/XPS
    board; ±0.5 mm thickness is typical for insulation-grade board (structural
    tooling board holds better — enter what YOUR datasheet says).
    """
    thickness_mm: float = 25.4
    length_mm: float = 1220.0
    width_mm: float = 610.0
    thickness_tol_mm: float = 0.5
    name: str = "NGX foam board"

    def __post_init__(self):
        if min(self.thickness_mm, self.length_mm, self.width_mm) <= 0:
            raise ValueError("sheet dimensions must be positive")
        if self.thickness_tol_mm < 0:
            raise ValueError("thickness tolerance cannot be negative")

    def area_m2(self) -> float:
        return (self.length_mm / 1000.0) * (self.width_mm / 1000.0)


@dataclass(frozen=True)
class FoamLayer:
    """One horizontal slab of the stack, bottom-up. The CUT outline is the slab's
    LARGER face (its bottom, on an upward-tapering body) — foam is sanded down to
    the loft, never grown back — and the top outline is printed dashed as the
    sand-to line so the shaping crew works toward a drawn target, not a guess."""
    index: int                       # 1-based, bottom layer first
    z_bottom_mm: float
    z_top_mm: float
    cut_outline: tuple               # ((x, y), ...) closed polygon at z_bottom
    sand_to_outline: tuple           # ((x, y), ...) closed polygon at z_top
    length_mm: float                 # bounding box of the cut outline
    width_mm: float

    @property
    def thickness_mm(self) -> float:
        return self.z_top_mm - self.z_bottom_mm

    def bbox_area_m2(self) -> float:
        return (self.length_mm / 1000.0) * (self.width_mm / 1000.0)


@dataclass(frozen=True)
class StackTolerance:
    """
    "Small dimension errors compound as the layers stack" — computed. Sheet
    thickness and bondline scatter are treated as independent (RSS is the
    expected error; worst-case is every error one way), and the template
    alignment scatter appears as a lateral step between layers — i.e. surface
    waviness the sanding pass must remove.
    """
    n_layers: int
    n_bondlines: int
    height_rss_mm: float
    height_worst_mm: float
    alignment_step_mm: float

    def feed(self, budget: ToleranceBudget, scaled_height_mm: float) -> ToleranceBudget:
        """
        Push the stack's own error into the scale-model ToleranceBudget, so the
        BUILD METHOD's uncertainty rides in the same C_l/C_d band the tunnel
        correlation reads. Height error enters as a fractional profile error
        (custom channel, basis recorded); alignment steps enter as waviness.
        """
        if scaled_height_mm > 0 and self.height_rss_mm > 0:
            frac = self.height_rss_mm / scaled_height_mm
            budget.add_custom(
                "foam-stack height (sheet+bondline stack-up)",
                cl_frac=frac, cd_frac=0.5 * frac, dev_mm=self.height_rss_mm,
                basis=f"RSS of {self.n_layers} sheet + {self.n_bondlines} bondline "
                      f"tolerances; fractional height error ~1:1 on profile")
        if self.alignment_step_mm > 0:
            budget.add_surface_waviness_mm(self.alignment_step_mm)
        return budget

    def summary(self) -> str:
        return (f"stack-up over {self.n_layers} layers / {self.n_bondlines} glue lines: "
                f"height ±{self.height_rss_mm:.1f} mm expected (RSS), "
                f"±{self.height_worst_mm:.1f} mm worst-case; "
                f"±{self.alignment_step_mm:.1f} mm layer-to-layer step to sand out")


@dataclass
class SlicePlan:
    """
    The slide-6 instruction made exact: the scaled body sliced into horizontal
    foam layers, each with its cut outline and sand-to line, plus the stack-up
    tolerance and the nesting area the BOM needs. Build with `SlicePlan.plan`.
    """
    body: NoseconeBody
    sheet: FoamSheet
    bondline_mm: float
    bondline_tol_mm: float
    template_alignment_tol_mm: float
    layers: tuple = ()
    top_trim_mm: float = 0.0          # sanded off the top layer to hit exact height
    tolerance: Optional[StackTolerance] = None

    @classmethod
    def plan(cls, body: NoseconeBody, sheet: FoamSheet,
             bondline_mm: float = 0.3, bondline_tol_mm: float = 0.2,
             template_alignment_tol_mm: float = 1.0,
             outline_pts: int = 80) -> "SlicePlan":
        """
        Slice bottom-up. Layer pitch = sheet thickness + bondline; the count is
        the smallest stack that REACHES the crest (the surplus is sanded off the
        top layer — reported as `top_trim_mm` so nobody wonders why the stack
        stands proud of the CAD before shaping).
        """
        if bondline_mm < 0 or bondline_tol_mm < 0 or template_alignment_tol_mm < 0:
            raise ValueError("bondline / tolerance inputs cannot be negative")
        h = body.base_height_mm
        pitch = sheet.thickness_mm + bondline_mm
        # smallest n with n*t + (n-1)*g >= H
        n = max(1, math.ceil((h + bondline_mm) / pitch))
        layers = []
        for i in range(n):
            z0 = i * pitch
            z1 = min(z0 + sheet.thickness_mm, h)
            if z1 <= z0:                      # degenerate top sliver fully above crest
                break
            cut = tuple(body.slice_outline(z0, outline_pts))
            sand = tuple(body.slice_outline(min(z1, h - 1e-9), outline_pts))
            if not cut:
                break
            xs = [p[0] for p in cut]
            ys = [p[1] for p in cut]
            layers.append(FoamLayer(
                index=i + 1, z_bottom_mm=z0, z_top_mm=z0 + sheet.thickness_mm,
                cut_outline=cut, sand_to_outline=sand,
                length_mm=max(xs) - min(xs), width_mm=max(ys) - min(ys)))
        n = len(layers)
        stack_height = n * sheet.thickness_mm + (n - 1) * bondline_mm
        tol = StackTolerance(
            n_layers=n, n_bondlines=max(0, n - 1),
            height_rss_mm=math.sqrt(n * sheet.thickness_tol_mm ** 2
                                    + max(0, n - 1) * bondline_tol_mm ** 2),
            height_worst_mm=n * sheet.thickness_tol_mm
                            + max(0, n - 1) * bondline_tol_mm,
            alignment_step_mm=template_alignment_tol_mm)
        return cls(body=body, sheet=sheet, bondline_mm=bondline_mm,
                   bondline_tol_mm=bondline_tol_mm,
                   template_alignment_tol_mm=template_alignment_tol_mm,
                   layers=tuple(layers), top_trim_mm=max(0.0, stack_height - h),
                   tolerance=tol)

    # -- BOM inputs ---------------------------------------------------------- #
    def n_layers(self) -> int:
        return len(self.layers)

    def bonded_area_m2(self) -> float:
        """Total glue-line area (each interface ~ the upper layer's cut bbox —
        conservative, so the adhesive order errs long, not short)."""
        return sum(l.bbox_area_m2() for l in self.layers[1:])

    def nesting_area_m2(self) -> float:
        """Sum of layer bounding boxes — what the sheets must yield after nesting."""
        return sum(l.bbox_area_m2() for l in self.layers)

    def sheets_needed(self, packing_efficiency: float = 0.75) -> int:
        """Stock sheets to order. ESTIMATE via bbox nesting at a stated packing
        efficiency (0.75 is honest for hand-nested irregular outlines)."""
        if not (0.3 <= packing_efficiency <= 1.0):
            raise ValueError("packing efficiency outside a sane 0.3–1.0")
        # a layer longer/wider than the sheet cannot be nested at all — say so
        for l in self.layers:
            long_side = max(l.length_mm, l.width_mm)
            short_side = min(l.length_mm, l.width_mm)
            if (long_side > max(self.sheet.length_mm, self.sheet.width_mm)
                    or short_side > min(self.sheet.length_mm, self.sheet.width_mm)):
                raise ValueError(
                    f"layer {l.index} ({l.length_mm:.0f}×{l.width_mm:.0f} mm) does not "
                    f"fit the {self.sheet.length_mm:g}×{self.sheet.width_mm:g} mm sheet — "
                    "split the layer or source larger stock")
        return max(1, math.ceil(self.nesting_area_m2()
                                / (self.sheet.area_m2() * packing_efficiency)))

    def top_layer_sliver(self) -> Optional[float]:
        """Effective loft thickness inside the top layer, if suspiciously thin.
        A <5 mm working sliver tears during shaping — flagged by the gate."""
        if not self.layers:
            return None
        top = self.layers[-1]
        remaining = self.body.base_height_mm - top.z_bottom_mm
        return remaining if remaining < 5.0 else None

    def summary(self) -> str:
        t = self.tolerance
        lines = [
            f"{self.n_layers()} layers of {self.sheet.thickness_mm:g} mm "
            f"{self.sheet.name} + {max(0, self.n_layers()-1)} × "
            f"{self.bondline_mm:g} mm bondlines reach "
            f"{self.body.base_height_mm:g} mm crest "
            f"(sand {self.top_trim_mm:.1f} mm off the top layer)",
            t.summary() if t else "",
        ]
        return "\n".join(s for s in lines if s)


# --------------------------------------------------------------------------- #
#  Templates — 1:1 SVGs the printer cannot silently betray
# --------------------------------------------------------------------------- #
def layer_template_svg(layer: FoamLayer, margin_mm: float = 15.0) -> str:
    """
    A printable 1:1 cutting template for one foam layer, in real millimetres:
    solid CUT line (the layer's larger face), dashed SAND-TO line (its top
    outline — the loft target), centreline + nose datum for stack alignment, and
    a 100 mm scale-verification bar. THE BAR IS THE CONTRACT: if it doesn't
    measure 100 mm off the printer, the printer rescaled the page and the
    template is scrap — print at "actual size", never "fit to page".
    """
    cut = list(layer.cut_outline)
    if not cut:
        raise ValueError("layer has an empty outline — nothing to template")
    xs = [p[0] for p in cut]
    ys = [p[1] for p in cut]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w = (x1 - x0) + 2 * margin_mm
    h = (y1 - y0) + 2 * margin_mm + 22.0          # room for the scale bar + text

    def _pts(poly):
        return " ".join(f"{(x - x0 + margin_mm):.2f},{(y - y0 + margin_mm):.2f}"
                        for x, y in poly)

    sand = list(layer.sand_to_outline)
    bar_y = (y1 - y0) + 2 * margin_mm + 6.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" '
        f'height="{h:.1f}mm" viewBox="0 0 {w:.2f} {h:.2f}">',
        '<style>text{font-family:sans-serif}</style>',
        # cut line
        f'<polygon points="{_pts(cut)}" fill="none" stroke="#000" stroke-width="0.5"/>',
    ]
    if sand:
        parts.append(f'<polygon points="{_pts(sand)}" fill="none" stroke="#666" '
                     'stroke-width="0.35" stroke-dasharray="3,2"/>')
    cx = margin_mm - (x0 - 0.0)  # nose tip x in page coords (tip is at body x)
    parts += [
        # centreline datum (y = 0) across the template
        f'<line x1="{margin_mm - 8:.2f}" y1="{(0 - y0 + margin_mm):.2f}" '
        f'x2="{(x1 - x0 + margin_mm + 8):.2f}" y2="{(0 - y0 + margin_mm):.2f}" '
        'stroke="#c00" stroke-width="0.3" stroke-dasharray="6,2,1,2"/>',
        # 100 mm scale-verification bar
        f'<line x1="{margin_mm:.2f}" y1="{bar_y:.2f}" x2="{margin_mm + 100:.2f}" '
        f'y2="{bar_y:.2f}" stroke="#000" stroke-width="0.8"/>',
        f'<line x1="{margin_mm:.2f}" y1="{bar_y - 2.5:.2f}" x2="{margin_mm:.2f}" '
        f'y2="{bar_y + 2.5:.2f}" stroke="#000" stroke-width="0.6"/>',
        f'<line x1="{margin_mm + 100:.2f}" y1="{bar_y - 2.5:.2f}" '
        f'x2="{margin_mm + 100:.2f}" y2="{bar_y + 2.5:.2f}" stroke="#000" stroke-width="0.6"/>',
        f'<text x="{margin_mm + 104:.2f}" y="{bar_y + 1.8:.2f}" font-size="4.5">'
        'must measure 100 mm — else the printer rescaled (use "actual size")</text>',
        f'<text x="{margin_mm:.2f}" y="{bar_y + 10:.2f}" font-size="4.5">'
        f'LAYER {layer.index} · z {layer.z_bottom_mm:.1f}–{layer.z_top_mm:.1f} mm · '
        f'solid = CUT · dashed = SAND-TO · red = centreline datum</text>',
        '</svg>',
    ]
    _ = cx  # (nose datum coincides with template left edge on this loft)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  Materials — "check the needed supplies asap" as arithmetic, not homework
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LayupRecipe:
    """
    The process assumptions the whole order hangs on — stated once, visible,
    editable. Defaults mirror the standard FSAE moldless workflow (hand layup,
    peel ply, coating resin built up then flatted): 200 gsm cloth, 1:1 wet-out
    on hand layup, 25% cutting waste, three coats of ~0.2 mm coating against a
    0.3 mm DA flatting pass.
    """
    plies: int = 2
    fabric_areal_gsm: float = 200.0
    resin_to_fiber_ratio: float = 1.0     # hand layup wet-out; infusion would be ~0.6
    fabric_waste_frac: float = 0.25
    coating_layers: int = 3
    coating_thickness_mm: float = 0.6     # TOTAL build across all coats
    coating_density_g_cm3: float = 1.1
    flatting_removal_mm: float = 0.3      # depth the 120-grit DA pass takes off
    resin_mix_loss_frac: float = 0.15     # cup/brush losses
    adhesive_coverage_ml_m2: float = 250.0  # PU adhesive, thin even spread

    def __post_init__(self):
        if self.plies < 1:
            raise ValueError("at least one ply")
        for name in ("fabric_areal_gsm", "coating_thickness_mm",
                     "coating_density_g_cm3", "adhesive_coverage_ml_m2"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not (0 <= self.fabric_waste_frac < 1) or not (0 <= self.resin_mix_loss_frac < 1):
            raise ValueError("waste/loss fractions must be in [0, 1)")

    def coating_margin_mm(self) -> float:
        """Coating build minus the flatting depth — what stands between the
        sander and the structural carbon. The gate wants this ≥ 0.2 mm."""
        return self.coating_thickness_mm - self.flatting_removal_mm


@dataclass(frozen=True)
class BOMLine:
    item: str
    quantity: float
    unit: str
    basis: str        # the assumption that produced the number — always shown

    def display_qty(self) -> str:
        if self.unit in ("sheets", "cans", "pcs", "pairs", "sets"):
            return f"{int(math.ceil(self.quantity))}"
        return f"{self.quantity:.2f}".rstrip("0").rstrip(".")


@dataclass
class MaterialsEstimate:
    """
    The ordering list, computed from the SAME geometry the templates were cut
    from — so the sheet count, the fabric metreage and the resin grams cannot
    drift from the plan the way a hand-maintained spreadsheet does. Every line
    carries its basis. Everything is an ESTIMATE with margin; nothing here is a
    structural claim.
    """
    lines: tuple = ()
    warnings: tuple = ()
    shell_area_m2: float = 0.0

    @classmethod
    def compute(cls, body: NoseconeBody, plan: SlicePlan, recipe: LayupRecipe,
                sheet: FoamSheet, crew_size: int = 6,
                packing_efficiency: float = 0.75) -> "MaterialsEstimate":
        if crew_size < 1:
            raise ValueError("crew_size must be at least 1")
        area = body.shell_area_m2()
        fab_area = area * recipe.plies * (1.0 + recipe.fabric_waste_frac)
        fiber_g = fab_area * recipe.fabric_areal_gsm
        lam_resin_g = fiber_g * recipe.resin_to_fiber_ratio * (1.0 + recipe.resin_mix_loss_frac)
        coat_g = (area * 1.0e4) * (recipe.coating_thickness_mm / 10.0) \
                 * recipe.coating_density_g_cm3 * (1.0 + recipe.resin_mix_loss_frac)
        # cm² × cm × g/cm³ — area(m²)→cm² is ×1e4, mm→cm is /10
        adhesive_ml = plan.bonded_area_m2() * recipe.adhesive_coverage_ml_m2 * 1.2
        consum_area = area * 1.15
        n_sheets = plan.sheets_needed(packing_efficiency)

        lines = (
            BOMLine(sheet.name, n_sheets, "sheets",
                    f"{plan.n_layers()} layer bboxes = {plan.nesting_area_m2():.2f} m² "
                    f"nested at {packing_efficiency:.0%} into "
                    f"{sheet.length_mm:g}×{sheet.width_mm:g} mm stock"),
            BOMLine("PU foam adhesive (e.g. Gorilla Glue)", adhesive_ml, "mL",
                    f"{plan.bonded_area_m2():.2f} m² glue lines × "
                    f"{recipe.adhesive_coverage_ml_m2:g} mL/m² + 20% margin"),
            BOMLine("release film", consum_area, "m²",
                    "shell area + 15% overlap/handling"),
            BOMLine("spray adhesive (e.g. 3M Super 77)",
                    max(1, math.ceil(consum_area / 4.0)), "cans",
                    "one can per ~4 m² barrier area"),
            BOMLine("peel ply", consum_area, "m²",
                    "shell area + 15% overlap/handling"),
            BOMLine(f"carbon fabric {recipe.fabric_areal_gsm:g} gsm", fab_area, "m²",
                    f"{area:.2f} m² shell × {recipe.plies} plies × "
                    f"(1+{recipe.fabric_waste_frac:.0%} cutting waste)"),
            BOMLine("laminating epoxy (e.g. EL2), resin+hardener", lam_resin_g, "g",
                    f"{fiber_g:.0f} g fibre × {recipe.resin_to_fiber_ratio:g}:1 wet-out "
                    f"+ {recipe.resin_mix_loss_frac:.0%} mixing loss"),
            BOMLine("coating resin (e.g. XCR)", coat_g, "g",
                    f"{recipe.coating_thickness_mm:g} mm total build over "
                    f"{area:.2f} m² at {recipe.coating_density_g_cm3:g} g/cm³ "
                    f"+ {recipe.resin_mix_loss_frac:.0%} loss "
                    f"({recipe.coating_layers} coats)"),
            BOMLine("sandpaper 120 grit (DA discs)", 10, "pcs",
                    "foam shaping + flatting pass"),
            BOMLine("sandpaper 400/800/1000/2000 grit", 4 * 5, "pcs",
                    "5 sheets per grit through the refinement sequence"),
            BOMLine("polishing compound (self-diminishing, e.g. NW1)", 1, "pcs",
                    "final mirror pass"),
            BOMLine("respirators / dust masks", crew_size, "pcs",
                    "one per crew member — foam and cured-resin dust are non-negotiable"),
            BOMLine("safety glasses", crew_size, "pairs", "one per crew member"),
            BOMLine("nitrile gloves", crew_size * 4, "pairs",
                    "≥4 changes each across glue-up, layup and coating"),
            BOMLine("mixing cups / sticks / brushes / squeegees", crew_size, "sets",
                    "one set per crew member on resin steps"),
        )
        warns = []
        if recipe.coating_margin_mm() < 0.2:
            warns.append(
                f"coating build ({recipe.coating_thickness_mm:g} mm) leaves only "
                f"{recipe.coating_margin_mm():.2f} mm over the {recipe.flatting_removal_mm:g} mm "
                "flatting pass — the DA sander WILL find the structural carbon. "
                "Add coats or lighten the flatting pass. (This is also a hard gate.)")
        return cls(lines=lines, warnings=tuple(warns), shell_area_m2=area)

    def to_csv(self) -> str:
        """Drop-in for the team's ordering spreadsheet."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Item", "Quantity", "Unit", "Basis (all quantities are estimates)"])
        for l in self.lines:
            w.writerow([l.item, l.display_qty(), l.unit, l.basis])
        return buf.getvalue()

    def summary(self) -> str:
        out = [f"materials for {self.shell_area_m2:.2f} m² shell "
               "(every quantity an ESTIMATE with stated basis):"]
        out += [f"  - {l.item}: {l.display_qty()} {l.unit}  [{l.basis}]" for l in self.lines]
        out += [f"  ⚠ {w}" for w in self.warnings]
        return "\n".join(out)


# --------------------------------------------------------------------------- #
#  The build day — slide 13 run through a scheduler that knows what a cure is
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BuildStep:
    """One shop step. `active_min` occupies a crew; `cure_min` then occupies only
    the CLOCK (the crew is free — that's the parallelism the slide's 'buffer
    time' hand-waves at, made explicit)."""
    key: str
    label: str
    crew: str                      # "cut" / "shape" / "layup" — slide 13's roles
    active_min: int
    cure_min: int = 0
    needs: tuple = ()
    note: str = ""


def default_build_day(plan: SlicePlan, recipe: LayupRecipe,
                      adhesive_cure_min: int = 120,
                      laminating_cure_min: int = 240) -> tuple:
    """
    The meeting's own workflow (slides 4–13) as a dependency graph, sized to the
    ACTUAL slice plan (cut time scales with layer count, layup with area). Cure
    defaults are honest mid-range figures for PU adhesive clamp time and a
    room-temperature laminating epoxy to green-stage — override with YOUR
    datasheet numbers; the schedule is only as honest as its cures.
    """
    n = plan.n_layers()
    layup_min = max(45, int(plan.body.shell_area_m2() * 90))   # ~90 min/m² hand layup
    return (
        BuildStep("templates", "Print & verify templates (100 mm bar!)", "shape", 30,
                  note="reject any sheet whose scale bar is off"),
        BuildStep("cut", f"Cut {n} foam layers to template", "cut",
                  max(30, 10 * n), needs=("templates",)),
        BuildStep("glue", "Glue-up & clamp the stack", "cut", 30,
                  cure_min=adhesive_cure_min, needs=("cut",),
                  note="stack against the centreline datum on every template"),
        BuildStep("prep_tools", "Prep shaping tools / CNC or profile templates",
                  "shape", 45, needs=("templates",)),
        BuildStep("rough", "Rough-shape the plug (saw/hot wire + 120 grit)",
                  "shape", 90, needs=("glue", "prep_tools"),
                  note="work to the dashed sand-to lines"),
        BuildStep("fine", "Fine-sand plug to profile templates", "shape", 60,
                  needs=("rough",)),
        BuildStep("release", "Release film + spray barrier", "layup", 30,
                  cure_min=20, needs=("fine",),
                  note="HARD GATE: no layup before this — or the foam never comes out"),
        BuildStep("peelply", "Apply peel ply", "layup", 20, needs=("release",)),
        BuildStep("layup1", "Hand layup — underside", "layup", layup_min,
                  cure_min=laminating_cure_min, needs=("peelply",)),
        BuildStep("edgeprep", "Cure-edge prep between stages", "layup", 20,
                  needs=("layup1",)),
        BuildStep("layup2", "Hand layup — top", "layup", layup_min,
                  cure_min=laminating_cure_min, needs=("edgeprep",)),
    )


@dataclass(frozen=True)
class ScheduledStep:
    step: BuildStep
    start_min: int          # minutes after day start
    active_end_min: int
    cure_end_min: int


@dataclass
class BuildDaySchedule:
    """
    Forward schedule of the build day: a step starts when every prerequisite's
    CURE has ended and its crew is free; the crew is released at active end.
    The verdict is the honest answer to "everything happens in one build day".
    """
    scheduled: tuple = ()
    day_minutes: int = 720
    day_start_label: str = "08:00"
    fits_in_day: bool = False
    finish_min: int = 0
    critical_path: tuple = ()
    verdict: str = ""
    suggestions: tuple = ()

    @classmethod
    def plan(cls, steps: Sequence[BuildStep], day_start: str = "08:00",
             day_end: str = "20:00") -> "BuildDaySchedule":
        def _mins(hhmm: str) -> int:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        day_minutes = _mins(day_end) - _mins(day_start)
        if day_minutes <= 0:
            raise ValueError("day_end must be after day_start")
        by_key = {s.key: s for s in steps}
        for s in steps:
            for need in s.needs:
                if need not in by_key:
                    raise ValueError(f"step '{s.key}' needs unknown step '{need}'")
        done: dict = {}          # key -> ScheduledStep
        crew_free: dict = {}     # crew -> minute the crew frees up
        remaining = list(steps)
        while remaining:
            progressed = False
            for s in list(remaining):
                if all(n in done for n in s.needs):
                    ready = max([done[n].cure_end_min for n in s.needs] or [0])
                    start = max(ready, crew_free.get(s.crew, 0))
                    a_end = start + s.active_min
                    c_end = a_end + s.cure_min
                    done[s.key] = ScheduledStep(s, start, a_end, c_end)
                    crew_free[s.crew] = a_end
                    remaining.remove(s)
                    progressed = True
            if not progressed:
                raise ValueError("dependency cycle in build steps: "
                                 + ", ".join(s.key for s in remaining))
        order = sorted(done.values(), key=lambda x: (x.start_min, x.step.key))
        finish = max(x.cure_end_min for x in order)
        # critical path: walk back from the latest-finishing step through the
        # prerequisite (or crew wait) that set its start time
        crit, cur = [], max(order, key=lambda x: x.cure_end_min)
        while cur is not None:
            crit.append(cur.step.key)
            preds = [done[n] for n in cur.step.needs]
            binding = [p for p in preds if p.cure_end_min == cur.start_min]
            if not binding and preds:
                binding = [max(preds, key=lambda p: p.cure_end_min)]
                if binding[0].cure_end_min < cur.start_min:
                    binding = []           # start set by crew availability, stop here
            cur = binding[0] if binding else None
        crit.reverse()
        fits = finish <= day_minutes
        over = finish - day_minutes
        sugg = []
        if not fits:
            sugg.append(f"runs {over} min past {day_end} — the slide-13 single-day "
                        "plan does not survive the cure times as entered")
            if "glue" in crit:
                sugg.append("adhesive cure is on the critical path: glue the stack "
                            "the evening before and start the day at 'rough shape'")
            if "layup2" in crit or "layup1" in crit:
                sugg.append("second layup stage's cure ends after hours: leave it "
                            "curing overnight and demold next morning, or switch "
                            "to a fast-hardener laminating system (update cure_min "
                            "from ITS datasheet)")
        cure_wait = sum(max(0, x.cure_end_min - x.active_end_min) for x in order
                        if x.step.key in crit)
        verdict = (f"finishes at +{finish//60}h{finish%60:02d} "
                   f"({'fits' if fits else 'DOES NOT FIT'} the "
                   f"{day_start}–{day_end} day); critical path: "
                   + " → ".join(crit)
                   + f"; {cure_wait} min of it is passive cure — crews are free "
                     "then, the clock is not")
        return cls(scheduled=tuple(order), day_minutes=day_minutes,
                   day_start_label=day_start, fits_in_day=fits, finish_min=finish,
                   critical_path=tuple(crit), verdict=verdict,
                   suggestions=tuple(sugg))

    def clock(self, minute: int) -> str:
        h0, m0 = self.day_start_label.split(":")
        t = int(h0) * 60 + int(m0) + minute
        return f"{(t // 60) % 24:02d}:{t % 60:02d}"

    def timeline(self) -> str:
        rows = []
        for x in self.scheduled:
            cure = (f", cure to {self.clock(x.cure_end_min)}" if x.step.cure_min else "")
            rows.append(f"  {self.clock(x.start_min)}–{self.clock(x.active_end_min)} "
                        f"[{x.step.crew:>5}] {x.step.label}{cure}")
        return "\n".join(rows)


# --------------------------------------------------------------------------- #
#  Preflight gates — the two rules that scrap a part, plus honest advisories
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GateItem:
    ok: bool
    severity: str          # "gate" (go/no-go) or "advisory"
    text: str


@dataclass
class PreflightGate:
    """Go/no-go before resin is mixed — the plug-build analogue of the
    manufacturing-release gate. A failed 'gate' item means STOP; an advisory
    means know what you're accepting."""
    items: tuple = ()

    @classmethod
    def check(cls, plan: SlicePlan, recipe: LayupRecipe,
              schedule: Optional[BuildDaySchedule] = None,
              similitude: Optional[SimilitudePlan] = None,
              crew_size: int = 0) -> "PreflightGate":
        items = []
        # 1. release barrier strictly before any layup (order in the schedule)
        if schedule is not None:
            byk = {x.step.key: x for x in schedule.scheduled}
            rel, l1 = byk.get("release"), byk.get("layup1")
            if rel is None or l1 is None:
                items.append(GateItem(False, "gate",
                    "schedule is missing the release-barrier or layup step — the "
                    "one skipped step that welds the shell to its foam forever"))
            else:
                ok = rel.cure_end_min <= l1.start_min
                items.append(GateItem(ok, "gate",
                    "release barrier (film + spray) completes before layup begins"
                    if ok else
                    "layup is scheduled BEFORE the release barrier has flashed — "
                    "the foam core will not come out; fix the schedule"))
        # 2. never sand into the structural carbon
        margin = recipe.coating_margin_mm()
        items.append(GateItem(margin >= 0.2, "gate",
            f"coating build {recipe.coating_thickness_mm:g} mm vs "
            f"{recipe.flatting_removal_mm:g} mm flatting: {margin:.2f} mm margin "
            + ("over the structural plies" if margin >= 0.2 else
               "— TOO THIN; the flatting pass will cut structural carbon. "
               "Add coating layers or lighten the pass")))
        # 3. PPE is a gate, not a nicety
        items.append(GateItem(crew_size > 0, "gate",
            f"PPE quantified for a crew of {crew_size} (respirators, glasses, gloves "
            "on the BOM)" if crew_size > 0 else
            "crew size not set — PPE unquantified; foam and cured-resin dust are "
            "respirable, this is not optional"))
        # 4. stack-up advisory
        t = plan.tolerance
        if t is not None:
            frac = t.height_worst_mm / plan.body.base_height_mm
            items.append(GateItem(frac <= 0.01, "advisory",
                f"worst-case stack height error ±{t.height_worst_mm:.1f} mm is "
                f"{frac:.1%} of crest height"
                + ("" if frac <= 0.01 else
                   " — measure the glued stack BEFORE shaping and correct at the "
                   "top layer; the error is already in the ToleranceBudget handoff")))
        # 5. sliver top layer
        sliver = plan.top_layer_sliver()
        items.append(GateItem(sliver is None, "advisory",
            "top layer carries a workable thickness" if sliver is None else
            f"top layer's working loft is only {sliver:.1f} mm — a sliver that "
            "tears during shaping; consider a thinner top sheet or shifting the "
            "bondline plan"))
        # 6. similitude, if a tunnel run is the point of the exercise
        if similitude is not None:
            items.append(GateItem(similitude.reachable, "advisory",
                f"similitude: {similitude.verdict}"))
        return cls(items=tuple(items))

    @property
    def go(self) -> bool:
        return all(i.ok for i in self.items if i.severity == "gate")

    def summary(self) -> str:
        head = "PREFLIGHT: GO" if self.go else "PREFLIGHT: NO-GO"
        lines = [head]
        for i in self.items:
            mark = "✓" if i.ok else ("✗" if i.severity == "gate" else "⚠")
            lines.append(f"  {mark} [{i.severity}] {i.text}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  The whole plan, one object — the shop-floor sibling of ScaledRunPlan
# --------------------------------------------------------------------------- #
@dataclass
class PlugBuildPlan:
    body: NoseconeBody
    slices: SlicePlan
    recipe: LayupRecipe
    bom: MaterialsEstimate
    schedule: BuildDaySchedule
    gate: Optional[PreflightGate] = None
    scale_spec: Optional[ScaleSpec] = None
    similitude: Optional[SimilitudePlan] = None

    def tolerance_budget(self) -> Optional[ToleranceBudget]:
        """A ToleranceBudget pre-loaded with the BUILD METHOD's own stack-up
        error, ready for the as-built deviations to be added on top — the direct
        bridge into `scale_model` and the tunnel correlation downstream."""
        if self.scale_spec is None or self.slices.tolerance is None:
            return None
        budget = ToleranceBudget(self.scale_spec)
        self.slices.tolerance.feed(budget, self.body.base_height_mm)
        return budget

    def provenance(self) -> str:
        bits = [f"foam-stack plug, {self.slices.n_layers()} × "
                f"{self.slices.sheet.thickness_mm:g} mm {self.slices.sheet.name}",
                f"{self.recipe.plies}-ply hand layup + "
                f"{self.recipe.coating_thickness_mm:g} mm coating"]
        if self.scale_spec is not None:
            bits.insert(0, self.scale_spec.label())
        if self.slices.tolerance is not None:
            bits.append(f"stack ±{self.slices.tolerance.height_rss_mm:.1f} mm RSS")
        return "; ".join(bits)

    def report(self) -> str:
        parts = [
            f"PLUG BUILD PLAN — {self.body.label()}",
            "",
            "SLICE PLAN", self.slices.summary(), "",
            "MATERIALS", self.bom.summary(), "",
            "BUILD DAY", self.schedule.verdict, self.schedule.timeline(),
        ]
        if self.schedule.suggestions:
            parts += [""] + [f"  → {s}" for s in self.schedule.suggestions]
        if self.gate is not None:
            parts += ["", self.gate.summary()]
        parts += ["", f"provenance: {self.provenance()}",
                  "NOTE: every quantity, duration and tolerance above is an "
                  "engineering ESTIMATE with its basis stated. Validate cures "
                  "against your resin datasheets; validate the loft against the "
                  "real CAD sections before cutting."]
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  Streamlit panel — surfaced as an Aerodynamics-tab view; lazy-imports UI deps
# --------------------------------------------------------------------------- #
def render_streamlit_panel(scale_spec: Optional[ScaleSpec] = None,
                           similitude: Optional[SimilitudePlan] = None) -> None:
    """The full plug & layup planner as a Streamlit view. Kept in this module so
    the 1 MB app file only carries a three-line hook; imports UI deps lazily so
    the package stays import-light and unit-testable without Streamlit."""
    import streamlit as st

    st.markdown(
        '<p class="hint">Plan the <b>scaled nosecone / bodywork plug build</b> — the '
        'shop-floor half of the scaled-model programme. Slice the loft into foam '
        'layers with an honest <b>stack-up tolerance</b>, print <b>1:1 templates</b> '
        'with a scale-check bar, get a computed <b>ordering list</b> (CSV for the '
        'supplies sheet), and run the build-day plan through a scheduler that knows a '
        '<b>cure frees the crew but not the clock</b>. Two hard gates guard the '
        'classic scrapped-part mistakes: release barrier before layup, and coating '
        'thick enough that flatting never touches structural carbon. The stack-up '
        'error feeds straight into the Scale-model planner\'s tolerance budget.</p>',
        unsafe_allow_html=True)

    with st.expander("1 · Scaled loft geometry", expanded=True):
        c = st.columns(4)
        L = c[0].number_input("Length (mm)", 100.0, 2000.0, 520.0, 10.0,
                              key="pb_len", help="Nose tip to base, of the SCALED part.")
        W = c[1].number_input("Base width (mm)", 50.0, 1000.0, 250.0, 5.0, key="pb_w")
        H = c[2].number_input("Base height (mm)", 50.0, 1000.0, 260.0, 5.0, key="pb_h")
        blunt = c[3].slider("Nose bluntness", 0.4, 1.0, 0.6, 0.05, key="pb_blunt",
                            help="Plan/height power-law exponent: 0.5 ≈ blunt "
                                 "elliptic nose, 1.0 = straight cone. A stand-in "
                                 "loft — swap in CAD sections via "
                                 "NoseconeBody.from_sections for the real part.")
        body = NoseconeBody(length_mm=L, base_width_mm=W, base_height_mm=H,
                            nose_exponent=blunt, height_exponent=min(1.0, blunt + 0.15))
        m = st.columns(3)
        m[0].metric("Shell area", f"{body.shell_area_m2():.2f} m²",
                    help="Numeric take-off of the outer skin incl. underside — "
                         "drives fabric, resin and consumable quantities. ESTIMATE.")
        m[1].metric("Plug volume", f"{body.volume_l():.1f} L")
        if scale_spec is not None:
            m[2].info(f"linked scale: {scale_spec.label()}")

    with st.expander("2 · Foam stock & slice plan", expanded=True):
        c = st.columns(5)
        t = c[0].number_input("Sheet thickness (mm)", 5.0, 100.0, 25.4, 0.1, key="pb_t")
        sl = c[1].number_input("Sheet length (mm)", 300.0, 3000.0, 1220.0, 10.0, key="pb_sl")
        sw = c[2].number_input("Sheet width (mm)", 200.0, 2000.0, 610.0, 10.0, key="pb_sw")
        ttol = c[3].number_input("Thickness tol ± (mm)", 0.0, 3.0, 0.5, 0.1, key="pb_ttol",
                                 help="From YOUR board's datasheet — this is what "
                                      "compounds up the stack.")
        bond = c[4].number_input("Bondline (mm)", 0.0, 3.0, 0.3, 0.1, key="pb_bond")
        sheet = FoamSheet(thickness_mm=t, length_mm=sl, width_mm=sw, thickness_tol_mm=ttol)
        try:
            plan = SlicePlan.plan(body, sheet, bondline_mm=bond)
        except ValueError as e:
            st.error(f"Slice plan error: {e}")
            return
        st.success(plan.summary().replace("\n", "  \n"))
        rows = [{"Layer": l.index,
                 "z (mm)": f"{l.z_bottom_mm:.1f}–{l.z_top_mm:.1f}",
                 "Template L×W (mm)": f"{l.length_mm:.0f} × {l.width_mm:.0f}"}
                for l in plan.layers]
        st.dataframe(rows, width='stretch')

        st.markdown("**1:1 cutting templates** — solid = cut, dashed = sand-to, "
                    "red = centreline. Print at *actual size* and **measure the "
                    "100 mm bar** before cutting anything.")
        pick = st.selectbox("Layer", [l.index for l in plan.layers], key="pb_layer")
        lay = plan.layers[pick - 1]
        st.download_button(f"⬇ template — layer {pick} (SVG, 1:1 mm)",
                           layer_template_svg(lay),
                           file_name=f"nosecone_layer_{pick:02d}_template.svg",
                           mime="image/svg+xml", key="pb_dl_one")
        import zipfile
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
            for l in plan.layers:
                z.writestr(f"nosecone_layer_{l.index:02d}_template.svg",
                           layer_template_svg(l))
        st.download_button("⬇ all templates (zip)", zbuf.getvalue(),
                           file_name="nosecone_layer_templates.zip",
                           mime="application/zip", key="pb_dl_zip")

    with st.expander("3 · Layup recipe & ordering list", expanded=True):
        c = st.columns(5)
        plies = c[0].number_input("Plies", 1, 8, 2, key="pb_plies")
        gsm = c[1].number_input("Fabric (gsm)", 80.0, 650.0, 200.0, 10.0, key="pb_gsm")
        coats = c[2].number_input("Coating coats", 1, 6, 3, key="pb_coats")
        cthk = c[3].number_input("Coating build (mm)", 0.1, 2.0, 0.6, 0.05, key="pb_cthk",
                                 help="TOTAL coating thickness across all coats.")
        crew = c[4].number_input("Crew size", 1, 20, 6, key="pb_crew")
        recipe = LayupRecipe(plies=int(plies), fabric_areal_gsm=gsm,
                             coating_layers=int(coats), coating_thickness_mm=cthk)
        bom = MaterialsEstimate.compute(body, plan, recipe, sheet, crew_size=int(crew))
        st.dataframe([{"Item": l.item, "Qty": l.display_qty(), "Unit": l.unit,
                       "Basis": l.basis} for l in bom.lines],
                     width='stretch')
        for w_ in bom.warnings:
            st.warning(w_)
        st.download_button("⬇ ordering list (CSV — paste into the supplies sheet)",
                           bom.to_csv(), file_name="plug_build_order.csv",
                           mime="text/csv", key="pb_dl_bom")

    with st.expander("4 · Build-day schedule", expanded=True):
        c = st.columns(4)
        start = c[0].text_input("Day start", "08:00", key="pb_start")
        end = c[1].text_input("Day end", "20:00", key="pb_end")
        glue_cure = c[2].number_input("Adhesive cure (min)", 15, 720, 120, 15,
                                      key="pb_gcure",
                                      help="Clamp time from YOUR adhesive's datasheet.")
        lam_cure = c[3].number_input("Laminating cure (min)", 30, 1440, 240, 30,
                                     key="pb_lcure",
                                     help="To green-stage, from YOUR resin's datasheet.")
        try:
            sched = BuildDaySchedule.plan(
                default_build_day(plan, recipe, adhesive_cure_min=int(glue_cure),
                                  laminating_cure_min=int(lam_cure)),
                day_start=start, day_end=end)
        except ValueError as e:
            st.error(f"Schedule error: {e}")
            return
        (st.success if sched.fits_in_day else st.error)(sched.verdict)
        for s in sched.suggestions:
            st.warning(f"→ {s}")
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            labels = []
            for x in reversed(sched.scheduled):
                labels.append(x.step.label)
                fig.add_trace(go.Bar(
                    y=[x.step.label], x=[x.active_end_min - x.start_min],
                    base=[x.start_min], orientation="h",
                    marker_color={"cut": "#e8b23a", "shape": "#5aa9e6",
                                  "layup": "#7bd389"}.get(x.step.crew, "#999"),
                    name=x.step.crew, showlegend=False,
                    hovertemplate=(f"{x.step.label}<br>{sched.clock(x.start_min)}–"
                                   f"{sched.clock(x.active_end_min)} ({x.step.crew})"
                                   "<extra></extra>")))
                if x.step.cure_min:
                    fig.add_trace(go.Bar(
                        y=[x.step.label], x=[x.step.cure_min], base=[x.active_end_min],
                        orientation="h", marker_color="rgba(160,160,160,0.45)",
                        showlegend=False,
                        hovertemplate=(f"cure to {sched.clock(x.cure_end_min)}"
                                       "<extra></extra>")))
            fig.add_vline(x=sched.day_minutes, line_dash="dash", line_color="#d33")
            fig.update_layout(barmode="overlay", height=90 + 34 * len(labels),
                              xaxis_title=f"minutes after {start} (dashed = {end})",
                              paper_bgcolor="rgba(0,0,0,0)",
                              plot_bgcolor="rgba(0,0,0,0)",
                              font=dict(color="#cdd6df", size=11),
                              margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width='stretch', key="pb_gantt")
            st.markdown('<p class="hint">Grey tails are cures — crews are free '
                        'then; the clock is not.</p>', unsafe_allow_html=True)
        except Exception:
            st.text(sched.timeline())

    with st.expander("5 · Preflight gate & tolerance handoff", expanded=True):
        gate = PreflightGate.check(plan, recipe, schedule=sched,
                                   similitude=similitude, crew_size=int(crew))
        (st.success if gate.go else st.error)(
            "PREFLIGHT: GO" if gate.go else "PREFLIGHT: NO-GO")
        for i in gate.items:
            mark = "✅" if i.ok else ("❌" if i.severity == "gate" else "⚠️")
            st.markdown(f"{mark} **[{i.severity}]** {i.text}")
        if scale_spec is None:
            ratio = st.number_input(
                "Model scale ratio (model/full)", 0.10, 1.0, 0.4, 0.05,
                key="pb_ratio",
                help="Same convention as Scale model planning: 0.4 = 40% = 1:2.5. "
                     "Links the stack-up error into the coefficient tolerance "
                     "budget the tunnel correlation reads.")
            scale_spec = ScaleSpec(ratio=ratio, scaled_chord_mm=body.length_mm,
                                   scaled_height_mm=body.base_height_mm,
                                   scaled_width_mm=body.base_width_mm)
        build = PlugBuildPlan(body, plan, recipe, bom, sched, gate=gate,
                              scale_spec=scale_spec, similitude=similitude)
        st.session_state["pb_build_plan"] = build
        budget = build.tolerance_budget()
        if budget is not None:
            st.session_state["pb_tolerance_budget"] = budget
            st.info(f"Building at {scale_spec.label()} — the stack-up error is now "
                    "in the tolerance budget. Add the AS-BUILT deviations in "
                    "**Scale model planning** after the build:  \n"
                    f"`{budget.report().summary.splitlines()[0]}`")
        st.download_button("⬇ full build plan (txt)", build.report(),
                           file_name="plug_build_plan.txt", mime="text/plain",
                           key="pb_dl_plan")


__all__ = [
    "NoseconeBody", "FoamSheet", "FoamLayer", "StackTolerance", "SlicePlan",
    "layer_template_svg", "LayupRecipe", "BOMLine", "MaterialsEstimate",
    "BuildStep", "default_build_day", "ScheduledStep", "BuildDaySchedule",
    "GateItem", "PreflightGate", "PlugBuildPlan",
]
