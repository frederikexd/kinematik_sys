<!--
  KinematiK — Formula SAE / Formula EV full-car pre-validation platform
  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
  Open source. Original author: Frederik Thio, creator of KinematiK.
-->

# Frame Planner

**Where:** Chassis role → 🛠️ Design & Sizing → *Team Fit* tab → 🧱 Frame
Planner (or `suspension.tubeframe` from Python).
**What:** the 06/29 chassis meeting turned into three computed answers —
the triangulation / load-path audit with an interactive 3D wireframe, the
Size-C sourcing trade study, and the panel & attachment planner for the four
subteam briefs.

---

## The problem it solves

Every line of the 06/29 deck is computable, and none of it was computed:

> *"Main hoop support needs a triangulated load path to the lower side impact
> node. These tubes interrupting the load paths are illegal in 2027."*
> *"Suppliers don't normally have our smallest tube size (Size C, 1.2 mm wall)
> … when 1.2 mm tubing is offered, it is close to $10/ft. We might have to
> increase our Size C to match Size B (1.65 mm)."*
> *"How close together the mounting points need to be to keep bodywork stable,
> how strong attachment brackets need to be, where they need to be."*
> *"Keep seat removable." · "Same quick-release mounting methods as Chassis
> Floor."*

## 1 · Triangulation & load-path audit

Model the frame as nodes + tubes (demo frame button, or two CSVs: nodes
`id,x,y,z,label` and tubes `name,a,b,class,size`). The audit finds:

- **Open bays** — near-planar 4-node cycles with no diagonal. Each finding
  proposes the shorter diagonal with its length, spec (governed by the
  strictest member class on the bay), mass, and cost.
- **Mid-span landings** — a tube end that T-bones a straight member's
  interior instead of arriving at a node: the "tubes interrupting the load
  paths" condition. Hoop-class hosts are exempt (a hoop is a continuous bent
  tube; welded mid-length nodes are normal).
- **Load-path audit** — pick two nodes (defaults find "main hoop support" and
  "lower side impact" by label) and get a verdict: the shortest primary-tube
  path, with every node on it held to "participates in ≥ 1 tube triangle" and
  every traversed tube held to "not interrupted mid-span". Failures carry the
  concrete fix.

The 3D view renders the whole space frame as three None-separated Plotly line
traces (OK / open-bay red / interrupted amber) plus one node trace — constant
trace count regardless of frame size, so it stays light. Suggested diagonals
draw dashed red; untriangulated nodes draw red.

## 2 · Tube sizing & sourcing trade study

The frame graph knows every tube's length and spec, so the BOM rolls up per
size class: tube count, feet, mass, cost, sourcing risk, and whether the price
is a quote or still an estimate ($/ft is editable inline — entering a number
marks it quoted). Then the meeting's exact what-if, **"re-spec every Size C
into Size B"**, answers with Δmass / Δcost tube-by-tube and a one-click Apply.
Upward merges are always rules-clean; a downward merge lists every tube that
would fall below its member-class minimum. The equivalency screen checks any
alternative OD × wall the way the rulebook frames it: E·I and bending strength
must not decrease, wall must not fall below the absolute floor (2.0 mm hoops /
harness, 1.2 mm elsewhere).

## 3 · Panel & attachment planner

One calculation covers all four subteam briefs: a panel of known size /
material / thickness, fastened on a pitch, under pressure (½ρv²·Cp from top
speed, or an override) plus inertial g. You get per-fastener load, the strip
deflection between fasteners (w = 5qL⁴/384EI, I = t³/12 per unit width), and
the **maximum stable pitch** — aero's "how close together do the mounting
points need to be" in millimetres. Every shortlisted fastener family
(quarter-turn Dzus, Camloc, rivnut, welded nutplate…) is screened against the
per-fastener load with its quick-release flag, so "quick-release floor /
firewall" is a verdict, not a debate. Harness attachment loads resolve per
point at a chosen deceleration and belt geometry, shaped to drop straight into
the Brakes tab's bolt & bracket FoS screen; the seat-mount check answers
"removable AND strong enough" for a chosen mount count and fastener.

## Honesty rules

- Size classes are transcribed from the FSAE 2024-25 baseline — verify against
  the year you compete under (the meeting itself flags 2027 changes).
- Fastener capacities and g-cases are labelled judgement screening figures;
  confirm with vendor data and the rulebook before manufacture.
- Pre-validation only: this finds the missing diagonal and sizes the tab.
  ANSYS confirms the frame; a pull test confirms the bracket.

## Python

```python
from suspension import tubeframe as tf

g = tf.demo_frame()                          # or FrameGraph.from_csv(...)
g.load_path_audit("MHS", "SIL")              # slide 4, verbatim
g.consolidate_spec("C", "B")                 # slide 5, verbatim
tf.plan_panel_attachment("aero", 900, 450, 2.0,
                         "Carbon laminate (quasi-iso)", 150.0, speed_kph=110)
tf.harness_attachment_loads()                # slides 7/9
```

Tests: `tests/test_tubeframe.py` · Demo: `python demo_frame_planner.py`
