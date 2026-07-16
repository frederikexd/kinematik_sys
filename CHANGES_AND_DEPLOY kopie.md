# KinematiK — analytics hardening (deploy notes)

Build: `0.12-analytics-hardened`

## Latest round — 🧱 Frame Planner (Chassis / Team Fit tab)

The 06/29 chassis meeting, computed. New module `suspension/tubeframe.py`
models the space frame as a node/tube graph and answers the deck's three pain
points: (1) **triangulation / load-path audit** — open bays (near-planar
4-cycles with no diagonal), mid-span T-bone landings on straight members
(hoop hosts exempt: continuous bent tube), and the verbatim slide-4 question
"is there a continuously-triangulated load path from the main hoop support
node to the lower side impact node?", every failure carrying its concrete fix
(the missing diagonal with length / spec / mass / cost); (2) **Size-C sourcing
trade** — per-spec BOM (length, mass, cost, sourcing risk, quoted-vs-estimate
price), the one-click "re-spec every Size C into Size B" what-if with per-tube
Δmass/Δcost and rules-minimum enforcement, plus an alternative-tubing
equivalency screen (E·I and bending strength must not decrease, absolute wall
floors respected); (3) **panel & attachment planner** — per-fastener load,
strip deflection between fasteners, the maximum stable fastener pitch (aero's
"how close together" answered in mm), quick-release fastener family screening,
harness per-point loads shaped for the bolt & bracket FoS screen, and the
removable-seat mount verdict. UI lives at the top of the chassis Team Fit tab
behind three expanders; the 3D wireframe renders the whole frame as three
None-separated Scatter3d line traces + one node trace (constant trace count,
no per-tube traces, so no rendering lag), untriangulated bays vivid red,
interrupted members amber, suggested diagonals dashed red, weak nodes red.
Demo frame reproduces slide 4's exact defects out of the box. Rules tables
labelled with their transcription year; fastener capacities and g-cases
labelled judgement. Tests in `tests/test_tubeframe.py` (16); usage in
`FRAME_PLANNER_USAGE.md`; demo `demo_frame_planner.py`.

## Latest round — 🩺 PCB Doctor (Electronics tab)

The board-check panel screens *declared* traces; the new PCB Doctor screens the
board the team already *routed*. Drop a real `.kicad_pcb` (KiCad 5–9) into the
Electronics tab and it parses every segment/via/footprint/pour with a native,
span-preserving s-expression reader, assigns each net its current from the
integration ledger (every guess labelled and editable), and runs the physics
DRC never sees: IPC-2221 heating at the per-net bottleneck, Onderdonk fusing,
via-barrel ampacity, true IR-drop → brown-out by nodal analysis of the routed
copper mesh, copper opens, IPC-2221-B4 HV clearance, diff-pair skew, HV→pair
coupling on real geometry, and component-level derating (caps on hot copper,
under-rated fuses, connector pins). Every finding names the component/net,
explains why it fails on the car despite passing simulation, and carries the
numeric fix. One click re-traces the existing board: only under-sized
`(width …)` tokens are rewritten (byte-identical otherwise, reopens in KiCad
with routing intact), the patched geometry is re-diagnosed for a before/after
FAIL count, and both the patched file and a hand-off fix report download.
Diff-pair members are never auto-widened (width sets impedance) — prescribed
instead. A 📏 Trace Prescriber answers "how wide, which layer, how many vias?"
with no board file at all. New module `suspension/pcb_doctor.py` (numpy-only,
same honesty rules: field-solver quantities reported *not computed*); tests in
`tests/test_pcb_doctor.py`; usage in `PCB_DOCTOR_USAGE.md`.

## Latest round — feature-menu drill-down (Tab ▸ Sub-tab ▸ Feature)

The feature-collection tabs used a horizontal "View" radio that landed the
member straight on the first feature's full content — a wall of controls before
they'd picked anything. Those tabs now follow the team's sketched pattern:
land on a short MENU of feature names with NOTHING expanded, pick one, and only
that feature renders, with a "← All tools" control to step back. Same features,
same per-feature code — only how they're surfaced changes.

- New shared `feature_menu(tab_key, features, …)` helper: renders the menu,
  remembers the pick per tab in session_state, returns the active feature name
  (or None while on the menu). One-line swap from the old radio; the existing
  `if _view == "…"` dispatch is unchanged, so on the menu nothing matches and
  nothing heavy renders.
- Applied to the genuine feature-collection tabs: **Aerodynamics** (Downforce /
  Wing & diffuser / Aero map / Scale model), **Brakes** (Bias & lock-up /
  Hydraulic / Bolt & bracket / Pedal box / Rotor thermal / Documentation),
  **Integration** (Verdict Center / Ledger / CAD fit / Mount-point clash),
  **DFMEA** (Risk log / Dashboard / Action tracker). Each menu item carries a
  one-line description so the member knows what opens before clicking.
- Deliberately NOT applied to single-workflow tabs (Kinematics live hardpoints,
  EV comparison, the 3D-model viewport toggle) — hiding their primary content
  behind a menu would bury the thing the tab is for, not de-clutter it.

## Latest round — Verdict Center, merged docs & universal Mesh/DXF (UX)

**Category navigation (Category → Sub-tab → Feature).** The single biggest
overwhelm — a member facing 10-20 tabs after picking their role — is gone. Tabs
are now grouped by KIND OF WORK into 5 category tabs: 🧪 Testing & Simulation,
🛠️ Design & Sizing, ✅ Checks & Integration, 📄 Documentation, 📊 Data & Cost.
Each category opens into a sub-tab strip of just that kind of tool; a member's
own subteam tools float to the front of each category. So the hierarchy is
Category → Sub-tab → Feature — never more than a handful of choices at any one
level. This only changes which container each tab id nests inside; every tab
body runs unchanged (same physics, same features, same single source of truth),
and the per-tab tab_open analytics gating was generalised from the old "More
tools wrapper" to "category tab open AND sub-tab open". `_TAB_CATEGORIES` maps
every id to a category with a safety net so nothing can vanish.

**Pick-first gate.** Nothing below the role picker renders until the member
makes a deliberate choice: the category strip, sub-tabs and all 25 tab bodies
are held behind `st.stop()` until they pick a subteam, choose "Just looking —
show me the shared tabs", or turn on "All tabs". So a member lands on the
picker + 3D car, not on a wall of tabs. A `kk_entered` flag records the choice;
existing sessions that already have a real role saved open straight through
(no forced re-pick).

**Strict role filtering.** Once a subteam is picked, the member sees ONLY that
subteam's applicable tabs plus the general/shared spine (integration, analytics,
validation, registry, notes, weight, 3D model) — non-applicable tabs are no
longer shown in the nav at all (previously they floated to the back; now
they're out). `_ROLE_TABS` was widened first so strict filtering doesn't strand
anyone: cooling now includes the EV tab (where radiator sizing/CAD lives),
brakes includes lap time + GGV + tyre (to see brake balance on track), aero
includes lap/GGV/transient/setup, etc. Non-applicable tab BODIES still execute
(shared page state depends on them) inside a collapsed "⋯ Other tools" expander,
so nothing breaks and they stay reachable if genuinely needed — but the member's
category nav shows only what's theirs. "All tabs" power mode still shows all 25.


Goal: the myth-buster / verdict area was one long scroll — a member didn't
realise how much was there. It's now organised into pages and boxes, with no
physics or data-source changes (same single source of truth: the Integration
ledger, the registry, the session activity log).

- **Verdict Center** (Integration tab ▸ first view). A page picker acts like
  arrows to different pages: **Overview** (whole-car picture — one coloured box
  per subsystem with counts and the top headline), one **page per subsystem**,
  and the **Sanity-check** page (the unchanged deterministic myth-buster).
- **Three-box verdict per subsystem** — every subsystem page (and the Verdict
  sub-tab on each tab's documentation panel) shows one tidy box each for
  **✅ This works · 🔎 Take a closer look · 🛑 Pay attention to this**, built
  from live ledger findings, the declared interface, and busted assumptions.
- **Documentation, merged + template library.** Each subsystem's documentation
  panel is now three sub-tabs (**📄 Document · ✅ Verdict · 📐 Mesh & DXF**).
  The Document tab offers a small **library of doc templates** a member ticks
  on/off (design intent, assumptions, calc summary, test plan, manufacturing,
  risks, handover); picked sections merge with the declared numbers + activity
  into the existing report/PDF. The **sanity-check** (myth-buster) is built in.
- **Short-list to mesh + DXF, specialised per subsystem, from REAL geometry.**
  Each subsystem exports the actual 2-D section it takes into CAD, built from
  what its own tab COMPUTED — not defaults. Tabs publish via
  `publish_export_geometry()` and the exporter consumes only that (exactly the
  brakes rotor pattern: nothing until the tool runs). ALL EIGHT are wired: aero
  (full-size chord + t/c from the scale model → NACA section, two thicknesses),
  suspension (upright ball-joint span from the live hardpoints → mount-plate
  PCD + bolt count), accumulator (segment box from the real cell grid), and
  cooling / powertrain / chassis / data-acq derive their section from the
  member-entered dimensions in the 3D-model "drop your part — type its size in
  mm" entry (radiator core face, motor flange with real ⌀ + peak torque, node
  gusset, DAQ bracket). Brakes keeps its dedicated rotor Pareto short-list +
  half-section exporter and adds a caliper bracket. Nothing invents a default:
  a subsystem with no computed/entered geometry gates with a "run this tab's
  tool first" message — no section a member could extrude by mistake.
  Because these teams build straight off the DXF (import → extrude → validate
  in ANSYS, no prior CAD), every profile is run through a self-intersection
  guard so it imports as ONE clean closed contour; the UI confirms "✓ ready to
  extrude" or warns before download. Holes are separate closed loops, units are
  embedded ($INSUNITS) so it never comes in at 25.4×, and annotation sits on
  its own layer off the profile. DXF builder extended to multiple polylines +
  circles; all ezdxf-validated R12, and the aero airfoil was confirmed to
  import + extrude cleanly at correct scale in a real SolidWorks seat.
- **"Double-check before you commit" disclaimer** appended under every verdict,
  report and export surface via one shared `_vc_disclaimer()` helper.

All new code is spliced into `streamlit_app.py` (no new files, no new deps);
callers of `render_documentation_expander(...)` are unchanged (keyword-only,
backward compatible).


## Files in this package

- `streamlit_app.py` — main app (repo root)
- `suspension/analytics.py` — analytics module (deploy together with streamlit_app.py)
- `suspension/analytics_hardening.sql` — full analytics DB migration
- `fix_feature_funnel.sql` — standalone one-view fix (per-feature funnel)
- `requirements.txt` — pins streamlit>=1.58 and extra-streamlit-components
- `README.md`

## Deploy order

1. **Push `streamlit_app.py` and `suspension/analytics.py` together.**
   They are a matched pair — deploying one without the other causes an AttributeError.
2. **Run `suspension/analytics_hardening.sql` in Supabase.**
   Safe to re-run (drop-then-create, idempotent). Covers all views including
   the rewritten `v_retention` and fixed `v_time_to_first_result`.
3. **Confirm** build stamp in Usage section reads `0.12-analytics-hardened`
   and streamlit runtime reads `>= 1.58.0`.

## What was fixed this round

### total_users incremented by 2 on every reopen
- **Root cause (SQL):** `v_retention` per_user CTE resolved uid per row before
  grouping. A user whose cookie hadn't resolved on render 1 produced two
  different uid values (seed vs durable id), landing two rows in per_user and
  counting as two distinct people.
- **Fix:** Two-phase grouping. Phase 1 groups by session_id and takes
  `max(visitor_id)` — a cookie resolving on render 2 wins over the NULL from
  render 1. One session → one uid. Phase 2 aggregates by person.
- `total_users` is now `count(distinct session_id)` — every session ever.
  Increments by exactly 1 per reopen.

### returning_users stuck at 0 / not updating
- **Root cause (identity):** Cookie writes silently fail on Streamlit Cloud.
  44 sessions produced 43 distinct `ck-` ids — not durable. Every reopen
  minted a new seed so users never linked across visits.
- **Root cause (Python):** Early-exit guard fired on `"cookie (resolving…)"`
  and skipped the cookie block on render 2. The CookieManager never got a
  chance to read back the real id. `session_start` fired with the seed.
- **Root cause (Python):** `session_start` was emitted before visitor_id was
  stable, so the event logged with a throwaway id that differed from the
  durable id resolved one render later.
- **Fix (SQL):** `ck-` ids excluded from identity in `v_retention`. Only
  `fp-` fingerprint and named member used as durable identity. `returning_users`
  now counts return visits (`sum(visits - 1)`), not distinct people.
- **Fix (Python / streamlit_app.py):** Early-exit guard now only skips
  re-resolution when kind is confirmed durable. First-render branch no longer
  assigns seed to `_vid` — leaves None so fingerprint runs. Cookie-absent
  branch no longer mints `ck-` seeds.
- **Fix (Python / analytics.py):** `init()` defers `session_start` emit when
  `_ax_resolved_vid_kind == "cookie (resolving…)"`. Fires on render 2 with
  stable id.

### time-to-first-result showed 0 min / was inaccurate
- **Root cause:** `v_time_to_first_result` anchored off `session_start`, which
  fires on render 2 after other events including `first_result`. Produced
  negative deltas that averaged to zero.
- **Fix:** View now uses `min(occurred_at)` across all events as true session
  start. Guard `t_first_result >= t_start` excludes historical negatives.
- Display now shows seconds ("28 sec") instead of rounding to "0 min".

### UI changes
- "Return vs FSAE members" tile removed.
- FSAE roster size input removed.
- Time-to-first-result displays in seconds.

## Note on historical data

SQL view fixes are retroactive — they recompute from raw events already in the
database. No past events were fabricated or deleted. Python fixes affect future
sessions only.

## Identity strategy summary

| Id type | Durable? | Used for identity? |
|---|---|---|
| `fp-` fingerprint (IP+UA) | Yes — stable per device | ✅ Yes |
| Named member | Yes — most durable | ✅ Yes |
| `ck-` cookie | No — writes fail on Streamlit Cloud | ❌ Excluded |
| `session_id` fallback | No — new each session | counted in total_users only |
