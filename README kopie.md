<!--
  KinematiK — Formula SAE / Formula EV full-car pre-validation platform
  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
  Open source. Original author: Frederik Thio, creator of KinematiK.
-->

---
title: KinematiK
emoji: 🏎️
colorFrom: yellow
colorTo: gray
sdk: streamlit
sdk_version: 1.58.0
app_file: streamlit_app.py
pinned: false
license: agpl-3.0
---

# ◢ KinematiK

**KinematiK gets you to the right question. ANSYS gives you the right answer.**

Open-source full-car pre-validation platform for Formula SAE and Formula EV teams.
Born as a suspension kinematics tool. Now the engineering operating system for an entire formula car.

> *"Our brakes lead called it a saving grace."*
> — CSULB SAE

---

## What it is

KinematiK is **not** ADAMS. It is **not** ANSYS. It is the hour before them.

The most expensive class of error in motorsport engineering isn't a bad simulation — it's garbage inputs reaching a simulation tool and producing garbage outputs. Teams spend days debugging solvers that were never the problem. The problem was the spreadsheet three steps earlier: disconnected, unvalidated, passed around the team for years, with sign conventions nobody has verified and assumptions that silently contradict each other across subsystems.

KinematiK fixes the step before simulation. Every subsystem decision — suspension geometry, brake bias, accumulator topology, cooling sizing, BOM cost — lives in one connected environment. When the suspension lead changes a parameter that affects brake bias, the brakes lead sees it. When the powertrain output shifts weight distribution, the aero and suspension leads know. The decisions that used to live in disconnected spreadsheets and get lost between meetings now have a single source of truth.

When you commit to an ANSYS run, you are confident the inputs are right. You are not burning a $50,000/seat simulation licence finding a fault that was in the spreadsheet three steps earlier.

> ⚠️ **Always validate outputs with ANSYS, ADAMS, or MATLAB before manufacturing.** KinematiK is a pre-validation tool, not a replacement for full simulation. Every output is a starting point, not a final answer. The tool itself will tell you this.

---

## Coverage

One environment. Every subsystem. The entire car.

| Subsystem | What KinematiK does |
|---|---|
| **Suspension / Dynamics** | 3D constraint solver, camber gain, bump steer, roll centre migration, load transfer, grip balance, compliance, setup optimiser, GGV, transient, upright mount-plate DXF |
| **Aerodynamics** | Downforce & ground effect, wing/diffuser sizing, aero map, virtual wind tunnel, wing-section (airfoil) DXF |
| **EV Powertrain** | Motor architecture comparison, energy budget, regen, lap time, torque vectoring, motor-flange DXF |
| **Accumulator** | Cell sizing, pack topology, FSAE-EV rules checks, thermal model, electrical feasibility gate, segment-box DXF |
| **Brakes** | Bias & lock-up, hydraulic sizing, bolt & bracket FoS, rotor thermal, fade test, rotor optimiser + rotor DXF export, caliper-bracket DXF |
| **Chassis / Frame** | 3D model, team fit, weight & CG ledger, handover export, node-gusset DXF, **Frame Planner** (node/tube frame graph with 3D wireframe, triangulation & load-path audit with per-defect fixes, Size C→B sourcing trade study, alternative-tubing equivalency screen, panel & attachment planner for seat/harness/floor/firewall/aero panels) |
| **Cooling** | Thermal sizing, heatmap, cross-subsystem heat propagation, radiator-core DXF |
| **Electronics** | PCB copper survival, signal integrity, HV/LV checks, **PCB Doctor** (import a real `.kicad_pcb`, diagnose real-life failures with the guilty component named, one-click re-trace of under-sized copper, multi-layer Trace Prescriber), sensor/PCB-bracket DXF |
| **Data Acquisition** | Integration with car-level electrical budget, DAQ-bracket DXF |
| **Cost & BOM** | FSAE Cost event, auto-seeded from Integration ledger, CSV export |
| **Integration** | Cross-subsystem ledger, coupling graph, risk propagation, manufacturing-release gate, **Verdict Center** (per-subsystem works / look-closer / attention) |
| **DFMEA** | Live failure mode analysis, pre-seeded rows, RPN recompute, action tracker |
| **Registry** | Component source of truth, version history, sign-off, CAD provenance parsing |

---

## The one idea

**One car, not eight tools.**

Every subsystem declares what it weighs, draws, rejects and provides into a single **Integration ledger**. That one source feeds the 3D model, the lap sim, the heatmap and the cost BOM. Declare a number once and it propagates everywhere — the eight "we're ~12 kg" estimates can't quietly sum to 18 kg over the number suspension tuned to.

When any subsystem saves an interface edit, KinematiK walks the change through a coupling graph and shows — unprompted — which other subsystems' risk just moved. Bump the motor torque and you immediately see it load the upright and heat the cooling loop. Every effect carries an honest confidence tag: **measured** (a solver ran), **coupled** (a modelled physical edge), or **judgement** (engineering judgement, no backing physics). A measured edge is demoted if the data behind it is still an estimate. A green board never overstates what is known.

---

## Three moves to start

0. **Pick your subteam.** Nothing opens until you choose who you are. Once you
   pick, you see only your subteam's tabs plus the shared spine (Integration,
   Validation, Analytics, Registry, Notes, 3D Model), grouped into five simple
   categories (Testing, Design, Checks, Docs, Data) — never all 25 at once.
1. **Declare your interface.** In **Integration**, fill what your subteam owns (mass, CG, torque, heat, current, downforce) and untick *estimate* once a number is real. Everything downstream uses it.
2. **Watch it ripple.** KinematiK walks your change through the coupling graph and flags which other subsystems' risk just moved.
3. **Clear the cut.** Before a part goes to manufacture, run the **manufacturing-release gate** — a literal go/no-go that blocks any part still resting on an estimate or an unconfirmed load.

### Get a build-ready DXF (no CAD needed to start)

Every subsystem exports the real 2-D section it takes into CAD — a wing airfoil,
a mount/flange plate with bolt holes, a radiator core face — built from *your*
computed numbers. In your subsystem tab, open its own **"📐 … — mesh & DXF
export"** panel (it sits just below the documentation panel, mirroring the
Brakes tab's inline rotor export), pick a section, and download. In SolidWorks: **File ▸ Open ▸ DXF ▸ import as 2D sketch**, extrude,
then mesh in ANSYS. Units are embedded and every profile is checked to import as
one clean closed contour.

---

## Positioning

KinematiK sits between your team's engineering decisions and your simulation budget.

```
Team decisions → KinematiK (pre-validation) → ANSYS / ADAMS / MATLAB (verification) → Manufacturing
```

It does not replace simulation. It makes simulation more valuable by ensuring the inputs that reach it are organised, connected, and pre-validated. The sim becomes a verification of a number you already trust — not the place you discover it.

---

## Pricing

**Free for students and FSAE / Formula Student teams. Always.**

KinematiK is free for any student or university team, permanently. The student community is not the revenue model — it is the distribution model. Every FSAE graduate who used KinematiK and joins a professional team is a warm introduction to that team, not a lost customer.

Professional teams, consultancies, and enterprises: contact for pricing.

---

## Usage stats

- **555 total users** across SAE student teams
- **50% return rate** (279 of 555 came back) — without any retention mechanism, reminder emails, or onboarding
- **12 seconds** to first result
- **18 days** of recorded traffic

A 50% return rate among students with no obligation to come back is the only metric that matters at this stage. Students are brutally honest users. If it is not useful, they close the tab and never return.

---

## Architecture

**Kinematics engine** — architecture-agnostic multibody solver (`suspension/topology.py`). Rigid bodies defined by points, constraint primitives (distance links, ball/pin coincidence, prismatic slider, planar, revolute, rack translation, beam-axle roll), assembled into a `Mechanism` and solved by branch-stable Levenberg–Marquardt sweep.

**Topology library** (`suspension/topologies.py`) — double wishbone, MacPherson strut, multi-link (3/4/5-link), trailing arm, semi-trailing arm, solid axle (Panhard or Watts), twist-beam, truck steer linkage, and `from_links` for experimental corners.

**Vehicle dynamics layer** — roll-centre migration, anti-dive/anti-squat, load transfer, grip balance, all topology-independent via `GenericKinematics` adapter (`suspension/adapter.py`).

**Analytics** (`suspension/analytics.py`) — privacy-respecting usage tracking. Durable identity via IP+UA fingerprint (cookie writes not reliable on Streamlit Cloud). All tracking is anonymous. No personal data stored.

---

## Database setup

Run `suspension/analytics_hardening.sql` in Supabase once. Safe to re-run (drop-then-create, idempotent grants). This creates all analytics views including the fixed `v_retention` and `v_time_to_first_result`.

For the per-feature funnel fix only, run `fix_feature_funnel.sql` standalone.

---

## Deploy order

1. Push `streamlit_app.py` and `suspension/analytics.py` together — they are a matched pair.
2. Run `suspension/analytics_hardening.sql` in Supabase.
3. Confirm build stamp in the Usage section reads `0.12-analytics-hardened` and streamlit runtime reads `>= 1.58.0`.

---

## What changed in this build (`0.12-analytics-hardened`)

**`v_retention` — complete rewrite (two-phase identity resolution)**
- Previous version grouped by `visitor_id` per row before aggregation. A user whose cookie resolved mid-session produced two different uid values and counted as two people, inflating `total_users` by 2 on every reopen.
- New version: phase 1 groups by `session_id` and takes `max(visitor_id)` so a cookie resolving on render 2 wins over the NULL from render 1 — one session, one uid, always. Phase 2 aggregates sessions by person.
- `ck-` cookie ids excluded from identity linking. Cookie writes silently fail on Streamlit Cloud (44 sessions / 43 distinct ids confirmed). Only `fp-` fingerprint and named member are used as durable identity.
- `total_users` now counts `count(distinct session_id)` — every session ever, including anonymous. Increments by exactly 1 per reopen.
- `returning_users` now counts return **visits** (`sum(visits - 1)`), not distinct people. Increments by 1 every time an identified user reopens.

**`v_time_to_first_result` — anchor fix**
- Previous version used `session_start` as `t_start`. Because `session_start` is deferred to render 2, `first_result` events often fired before it, producing negative deltas that averaged to a misleading result.
- New version uses `min(occurred_at)` across all events as the true session start. Added `t_first_result >= t_start` guard to exclude any historical negatives.
- Display now shows seconds (e.g. "28 sec") instead of rounding to "0 min".

**`streamlit_app.py` — visitor identity fixes**
- Early-exit guard now only skips re-resolution when `_ax_resolved_vid_kind` is a confirmed durable value. Previously fired on `"cookie (resolving…)"`, blocking the cookie block on render 2 entirely.
- First-render branch no longer assigns `ck-` seed to `_vid`. Leaves it `None` so the fingerprint fallback runs instead.
- Cookie-absent branch no longer mints a new `ck-` seed. Cookie writes fail silently; minting seeds produced a unique id per session that never linked across visits.
- "Return vs FSAE members" tile and roster size input removed.
- Time-to-first-result display changed to seconds.

**`suspension/analytics.py` — session_start deferral**
- `init()` now checks `_ax_resolved_vid_kind` before emitting `session_start`. If still `"cookie (resolving…)"`, defers to next rerun so `session_start` fires with a stable `visitor_id`.

---

## IP and attribution

KinematiK is the original work of Frederik Thio, developed independently as a personal project. Development history is timestamped in the Git commit log.

All outputs are for design direction. Always validate with full simulation (ANSYS, ADAMS, MATLAB) before manufacturing. This is not a suggestion — it is the entire point of the tool.

---

## License

AGPL-3.0. Free to use, fork, and build on. Any modifications must be shared under the same license.

© 2026 Frederik Thio
