<!--
  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
  Open source. Original author: Frederik Thio, creator of KinematiK.
-->

# Cross-Discipline Risk Propagation

`suspension/risk_propagation.py` is the layer that makes KinematiK's eight
subsystems behave like one car. It connects the two halves that already existed
but never talked to each other:

* **`interfaces.py`** — the cross-subsystem ledger (what each subsystem NEEDS and
  PROVIDES), and
* **`dfmea.py`** — the living RPN risk log.

When a sub-team changes one number — motor torque, CG height, pack heat, aero
downforce — this module walks the **coupling graph** and tells you *which
downstream risks just changed, by how much, and how much to trust it.*

## Why it exists

A big team has a systems engineer whose whole job is to hold the cross-discipline
dependency graph in their head. An underfunded team doesn't. This writes that
graph down once, so a freshman who bumps the motor map immediately sees that it
just loaded the upright, heated the cooling loop, and pushed two DFMEA rows into
a higher RPN band — *before* assembly or competition, not after.

## The honesty contract

Every propagated effect carries a confidence tag, and the module never claims
more than it can back:

* **measured** — a real KinematiK solver produced the number (mass→lap time via
  the mass roll-up; torque vs the declared driveline/upright limit; heat vs
  installed cooling capacity).
* **coupled** — a modelled physical edge, directional and closed-form, but not a
  full simulation.
* **judgement** — an engineering-judgement coupling with no backing physics.

If a solver can't run because the backing data isn't declared yet, the edge is
**demoted** from measured to coupled with a note — a green board never implies
more certainty than the data behind it. This is the same principle the rest of
KinematiK lives by.

> ⚠ **Always confirm with your subsystem lead before manufacturing.** This module
> and its release gate are a safety net, not a sign-off. The coupling graph
> (`COUPLINGS`) is a sensible default set of edges, not a guarantee that every
> load path on *your* car is captured — one may be missing, or wrong for your
> geometry. A "measured" tag means a solver ran on the numbers you entered, not
> that the design is correct. Before committing any part to manufacture, have the
> lead sanity-check both the overall approach and the specific edges loading that
> part. Treat the tool's verdict as input to that conversation, never a
> replacement for it.

## Quick start

```python
from suspension.interfaces import SubsystemInterface, blank_ledger
from suspension.risk_propagation import (
    propagate_interface_edit, dfmea_deltas, build_propagation_markdown,
)
from suspension import dfmea

led = blank_ledger()
led.driveline_torque_limit_nm = 200.0       # what the CV/driveshaft is rated for
led.total_cooling_airflow_cms = 0.18        # what the cooling pkg can move

# A sub-team saves an edit: more torque, hotter motor.
old = SubsystemInterface(name="powertrain", peak_torque_nm=150.0, heat_reject_w=1500.0)
new = SubsystemInterface(name="powertrain", peak_torque_nm=230.0, heat_reject_w=2600.0)

report = propagate_interface_edit(led, old, new)

for e in report.effects:
    print(e.headline(), "|", e.confidence.label)

# Land it on the team's existing DFMEA log (matches their own wording):
for s in dfmea_deltas(report, dfmea.seed_rows()):
    print(s["failure_mode"], s["rpn_old"], "->", s["rpn_suggested"])

# Design-review-ready brief:
print(build_propagation_markdown(report, team_name="Elbee Racing", season="2026"))
```

## How it pairs with the rest of the app

* `interfaces.diff_interfaces()` already produces the human-readable change log
  for an edit; `propagate_interface_edit()` consumes the *same* edit and adds the
  downstream risk. Call both on save.
* `dfmea_deltas()` returns **suggestions only** — an Occurrence nudge with the
  RPN recomputed through `dfmea.compute_rpn`. The human still owns the log; the
  tool just stops a coupled risk from going unnoticed.
* `coupling_catalog()` returns the whole graph as plain dicts for an in-app
  reference tab — itself a piece of documentation of how the car fits together,
  which is exactly the systems-engineering story design judges reward.

## Extending the graph

Add a `Coupling(...)` to `COUPLINGS`. Each edge names the source subsystem +
channel, the affected subsystem, the mechanism in plain language (naming **both**
disciplines, so the risk has an owner), the DFMEA failure modes it touches, and a
confidence tag. Attach a `solver` callable only when KinematiK genuinely owns the
physics — otherwise leave it off and tag it `judgement`. Keep it honest.
