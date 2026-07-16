# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Demo: the Aero Meeting #4 nosecone build, planned end-to-end in ~40 lines.

The meeting decided: a 1:2.5 scale nosecone plug from stacked NGX foam, glued,
shaped to templates, release-barriered, hand-laid in two stages, coated and
flatted — all in one build day, with supplies ordered "asap for next week".
This demo turns every one of those sentences into a number:

    python demo_plug_builder.py

Writes:  plug_build_order.csv          (the ordering list)
         nosecone_layer_01_template.svg (one example 1:1 template)
         plug_build_plan.txt            (the full report)
"""

from suspension.aero import (
    NoseconeBody, FoamSheet, SlicePlan, LayupRecipe, MaterialsEstimate,
    default_build_day, BuildDaySchedule, PreflightGate, PlugBuildPlan,
    layer_template_svg, ScaleSpec, SimilitudePlan,
)

# The scaled article (1:2.5 of the full nosecone), in shop millimetres.
body = NoseconeBody(length_mm=520, base_width_mm=250, base_height_mm=260,
                    note="Aero Meeting #4 — 1:2.5 nosecone plug")
sheet = FoamSheet(thickness_mm=25.4, length_mm=1220, width_mm=610,
                  thickness_tol_mm=0.5, name="NGX foam board 1in")

# 1) Slice plan — "each layer's outline becomes the cutting template",
#    with the stack-up tolerance the meeting told everyone to worry about.
plan = SlicePlan.plan(body, sheet, bondline_mm=0.3)
print(plan.summary(), "\n")

# 2) Templates — 1:1 mm SVG with the 100 mm printer-check bar.
with open("nosecone_layer_01_template.svg", "w") as f:
    f.write(layer_template_svg(plan.layers[0]))

# 3) The ordering list — "check the needed supplies asap", computed.
recipe = LayupRecipe(plies=2)
bom = MaterialsEstimate.compute(body, plan, recipe, sheet, crew_size=6)
print(bom.summary(), "\n")
with open("plug_build_order.csv", "w") as f:
    f.write(bom.to_csv())

# 4) The build day — slide 13 vs the actual cure times.
sched = BuildDaySchedule.plan(default_build_day(plan, recipe),
                              day_start="08:00", day_end="20:00")
print(sched.verdict)
print(sched.timeline())
for s in sched.suggestions:
    print("  →", s)
print()

# 5) Preflight gate + the similitude the tunnel run will need anyway.
spec = ScaleSpec(ratio=0.4, scaled_chord_mm=body.length_mm,
                 scaled_height_mm=body.base_height_mm,
                 scaled_width_mm=body.base_width_mm)
sim = SimilitudePlan.match_reynolds(spec, full_speed_ms=60 / 3.6,
                                    tunnel_max_speed_ms=45.0)
gate = PreflightGate.check(plan, recipe, schedule=sched, similitude=sim,
                           crew_size=6)
print(gate.summary(), "\n")

# 6) One object, one provenance string, one report.
build = PlugBuildPlan(body, plan, recipe, bom, sched, gate=gate,
                      scale_spec=spec, similitude=sim)
with open("plug_build_plan.txt", "w") as f:
    f.write(build.report())
print("provenance:", build.provenance())
print("\nstack-up already in the tolerance budget:")
print(build.tolerance_budget().report().summary)
