<!--
  KinematiK — Formula SAE / Formula EV full-car pre-validation platform
  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
  Open source. Original author: Frederik Thio, creator of KinematiK.
-->

# Plug & Layup Build Planner

**Where:** Aerodynamics tab → *Plug & layup build planner* (or
`suspension.aero.plug_builder` from Python).
**What:** the shop-floor half of a scaled aero build — foam slice plan with an
honest stack-up tolerance, 1:1 printable cutting templates, a computed ordering
list, a cure-aware build-day schedule, and a go/no-go preflight gate.

---

## The problem it solves

A scaled nosecone/bodywork build is planned, today, as prose on slides and a
disconnected supplies spreadsheet:

> *"Please check the needed supplies asap since we want to order for next week."*
> *"Slice the scaled body into horizontal layers … small dimension errors compound
> as the layers stack."*
> *"Build up [coating] thickness carefully so the structural carbon layers
> underneath are never sanded into."*
> *"Everything happens in one build day — plan buffer time between adhesive/resin
> cure steps … assign roles ahead of time."*

Every one of those sentences is computable, and none of it was computed anywhere.
KinematiK already owned the *tunnel* half of the scaled programme
(`scale_model.py`: similitude, tolerance → coefficient uncertainty, mount
alignment). This module owns the *make-the-thing* half, and hands its error
budget straight into the tunnel half.

## Five sections, one flow

1. **Scaled loft geometry** — a parametric nosecone loft (length × base width ×
   base height, nose bluntness). It's a stand-in good enough to slice, template
   and take off areas; when the team exports real CAD sections, swap them in via
   `NoseconeBody.from_sections(...)` and everything downstream uses them
   unchanged.

2. **Foam stock & slice plan** — enter the board you can actually buy (thickness
   **and its tolerance** — that's what compounds) and the bondline. You get the
   layer count that *reaches* the crest (surplus reported as top-layer trim,
   never a short stack), each layer's cut outline and z-band, and the stack-up
   tolerance: RSS *and* worst-case height error, plus the layer-to-layer
   alignment step the sanding pass must remove.
   **Templates:** per-layer 1:1-mm SVGs — solid cut line, *dashed sand-to line*
   (the loft target inside the slab, so the shaping crew works toward a drawn
   line, not a guess), red centreline datum for stacking, and a printed
   **100 mm scale-verification bar**. If the bar doesn't measure 100 mm off the
   printer, the printer rescaled the page and the template is scrap. Print at
   "actual size", never "fit to page".

3. **Layup recipe & ordering list** — ply count, fabric weight, coating build,
   crew size → a quantified BOM: foam sheets from actual layer nesting, adhesive
   from bonded area, fabric/laminating resin from the lofted shell area and
   wet-out ratio, coating resin from build thickness, sandpaper through the
   120→2000 sequence, and PPE scaled to the crew. Every line carries its basis;
   every number is an **estimate with margin**. Download as CSV and paste into
   the supplies sheet — the order can no longer drift from the plan.

4. **Build-day schedule** — the standard workflow (cut → glue → cure → shape →
   release barrier → peel ply → two-stage layup) as a dependency graph, sized to
   *your* layer count and area, scheduled against the three crews. The scheduler
   knows the one thing a slide can't show: **a cure frees the crew but not the
   clock**. With honest room-temperature cures the classic single-day plan
   usually does *not* fit — the verdict says so, names the critical path, and
   offers the standard fixes (glue the stack the evening before; demold next
   morning; or enter your fast-hardener's datasheet cure and re-check).

5. **Preflight gate & tolerance handoff** — go/no-go before resin is mixed:
   * **Gate:** release barrier scheduled strictly before any layup (skip it and
     the foam never comes out of the shell).
   * **Gate:** coating build ≥ flatting depth + 0.2 mm (or the DA sander finds
     the structural carbon).
   * **Gate:** PPE quantified for the crew.
   * Advisories: stack-up vs crest height, sliver top layers, and the Reynolds
     similitude verdict if a scale is linked.
   Set the model scale ratio and the stack-up error is pushed into a
   `ToleranceBudget` (session key `pb_tolerance_budget`) — the **same
   coefficient-uncertainty band the wind-tunnel correlation reads**. After the
   build, add the as-built deviations in *Scale model planning* on top of it.

## Python quick start

```python
from suspension.aero import (NoseconeBody, FoamSheet, SlicePlan, LayupRecipe,
                             MaterialsEstimate, default_build_day,
                             BuildDaySchedule, PreflightGate, PlugBuildPlan,
                             layer_template_svg)

body  = NoseconeBody(length_mm=520, base_width_mm=250, base_height_mm=260)
plan  = SlicePlan.plan(body, FoamSheet(thickness_mm=25.4, thickness_tol_mm=0.5))
print(plan.summary())                          # layers, glue lines, ± stack error

open("layer_01.svg", "w").write(layer_template_svg(plan.layers[0]))

bom   = MaterialsEstimate.compute(body, plan, LayupRecipe(plies=2),
                                  FoamSheet(), crew_size=6)
open("order.csv", "w").write(bom.to_csv())     # → the supplies sheet

sched = BuildDaySchedule.plan(default_build_day(plan, LayupRecipe()))
print(sched.verdict)                           # fits the day, or honestly doesn't

gate  = PreflightGate.check(plan, LayupRecipe(), schedule=sched, crew_size=6)
print(gate.summary())                          # GO / NO-GO with reasons
```

`demo_plug_builder.py` runs the whole flow on the meeting's own 1:2.5 nosecone
and writes the order CSV, an example template and the full plan report.

## Honesty notes (read before trusting any number)

* Every quantity, duration and tolerance is an engineering **ESTIMATE** whose
  basis is printed next to it. The BOM sizes an order, not a structure.
* Cure defaults (PU adhesive 120 min clamp, laminating epoxy 240 min to green)
  are honest mid-range figures — **override them with your resin/adhesive
  datasheets**; the schedule is only as honest as its cures.
* The parametric loft is a stand-in until CAD sections are supplied via
  `from_sections`. Validate the outlines against the real CAD before cutting.
* A gap this module reports — stack short, cure past midnight, coating thinner
  than the flatting pass — is a real gap. Fix the plan, not the report.

## Tests

`tests/test_plug_builder.py` — 32 tests covering the slice arithmetic, stack-up
RSS/worst-case, template scale honesty, BOM scaling laws, scheduler dependency/
crew/cure correctness (including the honest single-day failure), both hard
gates, and the `ToleranceBudget` handoff.
