# KinematiK — unified release notes

Build: `0.23.0-unified`

---

## New in `0.23.0-unified`

### ⚠️ Integrated DFMEA Risk Engine (`suspension/risk_engine.py`)
Rule-based risk propagation MATRIX over live chassis / brakes / powertrain /
cooling parameters. FoS floors and cooling-manifold localized-ΔP ceilings
elevate failure-mode Severity deterministically with the deficit; occurrence
propagates along declared cross-subsystem edges; RPN is computed live via
`dfmea.compute_rpn` so live rows and hand-curated rows can never disagree.
Includes the SLOTTED-HOLE fastener torque & preload calculator (brake pedal
tabs): Motosh long-form K recomputed over the actual slot-reduced under-head
contact patch, clamp force from `F = T/(K_eff·d)`, preload bearing-capped at
the reduced contact area. Tests: `tests/test_risk_engine.py`.

### 🔒 Multi-tenant workspace isolation (`suspension/workspace.py`,
`suspension/workspace_isolation.sql`)
Strict per-workspace sandboxing of accounts, parameters, configurations and
vehicle ledgers. SQL: `workspaces` + `workspace_members` spine, tenant-scoped
`kinematik_workspace_projects` (PK `(workspace_id, id)`), RLS ENABLED + FORCED
on every tenant table, membership-gated policies with `WITH CHECK`, legacy
table frozen, anon revoked. Python: query-side workspace filtering (defense in
depth over RLS), service-role key REFUSED, foreign-`workspace_id` payloads
rejected (`CrossWorkspaceViolation`), per-workspace local JSON fallback.
Run `suspension/workspace_isolation.sql` in Supabase (idempotent).
Tests: `tests/test_workspace.py`.

### ✅ Manufacturing-Release Gate (`suspension/release_gate.py`)
Deterministic gate over the active car ledger: chassis triangulation +
load-path audits, cooling manifold pressures + pump budget + vapor margin,
brake FoS + slotted pedal-tab clamp, torque specs inside grade-derived
(K/K_eff) windows. Missing evidence FAILS. IFF all green, generates the
printable "Tech Assembly & Torque Clipboard Checklist" PDF (reportlab) with
checkboxes, torque table, gate evidence and sign-off lines; generation is
refused (`GateNotPassed`) on a red gate. Tests: `tests/test_release_gate.py`.

---


## This release — four branches merged into one codebase

This build integrates four concurrent development branches into a single,
fully-unified, error-free codebase under the strict lazy-loading design
pattern (PEP 562 `__getattr__` facades throughout).

### Branches merged

| Branch | Contribution |
|---|---|
| `pe_out_plug_builder` | Base codebase: PCB Doctor, Plug Builder, analytics hardening, Verdict Center, feature-menu drill-down, universal Mesh/DXF |
| `kinematik_frame_planner` | `suspension/tubeframe.py` — Frame Planner: node/tube frame graph, triangulation/load-path audit, tube sourcing trade study, panel/attachment planner |
| `powertrain_engine` | `powertrain/` package: transient drivetrain simulator + Darcy–Weisbach cooling network solver (`powertrain/engine.py`), PEP 562 `powertrain/__init__.py` |
| `kinematik_lazy_refactor` | PEP 562 lazy facades for `suspension/__init__.py`, `suspension/aero/__init__.py`, `_LazyModule`/`_LazySymbol` in `streamlit_app.py`, `tests/test_lazy_layer.py` |

---

## New in `0.22.0-unified`

### 🧱 Frame Planner (`suspension/tubeframe.py`)

The 06/29 chassis meeting, computed. Three computable answers to three
chassis pain points:

1. **Triangulation & load-path audit** — open bays (near-planar 4-cycles
   with no diagonal), mid-span T-bone landings (hoop hosts exempt), and the
   verbatim slide-4 question "is there a continuously-triangulated load path
   from the main hoop support node to the lower side impact node?". Every
   failure carries a concrete fix: the missing diagonal with length / spec /
   mass / cost.
2. **Tube sizing & sourcing trade study** — per-spec BOM (length, mass, cost,
   sourcing risk, quoted-vs-estimate price), one-click "re-spec every Size C
   into Size B" with per-tube Δmass/Δcost and rules-minimum enforcement, plus
   alternative-tubing equivalency screen (E·I and bending strength must not
   decrease, absolute wall floors respected).
3. **Panel & attachment planner** — per-fastener load, strip deflection
   between fasteners, maximum stable fastener pitch (aero's "how close
   together" in mm), quick-release fastener family screening, harness
   per-point loads shaped for the bolt & bracket FoS screen, and the
   removable-seat mount verdict.

UI: 3D wireframe as three None-separated Scatter3d traces + one node trace
(constant trace count — no per-tube traces, no rendering lag). Untriangulated
bays red, interrupted members amber, suggested diagonals dashed red, weak
nodes red. Demo frame reproduces slide-4 defects out of the box.

**Where:** Chassis role → 🛠️ Design & Sizing → Team Fit tab → 🧱 Frame Planner.
**Python:** `from suspension import tubeframe as tf` or `suspension.FrameGraph`.
**Tests:** `tests/test_tubeframe.py` (16 tests). **Demo:** `demo_frame_planner.py`.
**Docs:** `FRAME_PLANNER_USAGE.md`.

### ⚙️ Powertrain Drive & Thermal (`powertrain/engine.py`)

New top-level `powertrain/` package with a PEP 562 lazy facade and a
complete `engine.py` module coupling two physics solvers:

1. **Transient drivetrain simulator** — forward-marching semi-implicit Euler
   launch simulation with motor torque curve, current limit, traction limit
   (with longitudinal load transfer), aero drag and rolling resistance.
   `optimize_gear_ratio` sweeps final-drive ratios for minimum time over a
   target distance. All outputs are plain numpy time-series sized for direct
   `st.line_chart` / plotly plotting.
2. **Cooling network analyzer** — Darcy–Weisbach pipe hydraulics,
   wye-junction loss/flow-split audits for the team's 29→29, 29→40 and
   29→12 mm y-branches, pump-vs-system operating point, and a
   lumped-capacitance transient coolant temperature march over a lap speed
   profile using effectiveness–NTU radiator heat rejection.
3. **Coupling** — `simulate_lap_thermal(..., drive=result)` uses the
   simulated speed and electrical-loss traces as the heat-generation input.
4. **Ledger duck-typing** — `total_mass_from_ledger` / `publish_to_ledger`
   accept the live `IntegrationLedger`, its `.as_dict()` form, or `None`.
   Zero hard dependency on the `suspension` package; importable standalone.

**Python:**
```python
from powertrain import MotorCurve, DrivetrainParams, simulate_launch
from powertrain import CoolingNetwork, PipeSegment, simulate_lap_thermal
```

### 🏎️ Lazy-import architecture (PEP 562) — full application

**`suspension/__init__.py`** — rewritten as a pure PEP 562 lazy facade.
`import suspension` is now microsecond-cost. Every submodule and re-exported
symbol loads on first attribute access only. The public API is completely
unchanged — every name that was importable before still is.

- `_SUBMODULES`: all 52 submodules exposed as `suspension.<name>` attributes,
  including `tubeframe` (new).
- `_FROM`: 220+ symbol→(submodule, original_name) entries covering the full
  public API including all new Frame Planner and powertrain symbols.
- `_ATTR_SUBMODULES`: union of `_SUBMODULES` and every home module in `_FROM`,
  so attribute access on any side-effect-bound submodule keeps working.

**`suspension/aero/__init__.py`** — rewritten as a PEP 562 lazy facade with
`_SYMBOL_HOME` fallback scan. All 70+ aero symbols (cfd, backends,
windtunnel, piv, pressure_tap, daq, scale_model, plug_builder, etc.) resolve
on first touch only.

**`streamlit_app.py`** — all heavyweight imports replaced with `_LazyModule`
and `_LazySymbol` proxies. The UI shell (page config, CSS, sidebar) paints
instantly before any engineering package is evaluated. The new
`powertrain` package is also lazy: `pt_engine_mod`, `MotorCurve`,
`DrivetrainParams`, `CoolingNetwork`, and all engine API symbols load only
when the Powertrain Drive & Thermal panel is rendered.

**`tests/test_lazy_layer.py`** — new test suite that:
1. Verifies `import suspension` is inert (no numpy/submodule loaded).
2. Validates `_LazyModule` defers until first attribute touch.
3. Validates `_LazySymbol` forwards call/attr/item/containment/iteration.
4. Confirms post-import hook fires exactly once.
5. Exercises the Frame Planner's cached-audit JSON key round-trip.
6. Tests `_SYMBOL_HOME` fallback scan degrades gracefully on missing modules.

---

## Files in this package

| File | Source / Note |
|---|---|
| `streamlit_app.py` | `lazy_refactor` base + `powertrain` lazy imports added |
| `suspension/__init__.py` | Unified PEP 562 facade (all four branches merged) |
| `suspension/aero/__init__.py` | Unified PEP 562 lazy aero facade |
| `suspension/tubeframe.py` | From `frame_planner` branch (new) |
| `powertrain/__init__.py` | From `powertrain_engine` branch (new top-level package) |
| `powertrain/engine.py` | From `powertrain_engine` branch (new) |
| `tests/test_tubeframe.py` | From `frame_planner` branch (new, 16 tests) |
| `tests/test_lazy_layer.py` | From `lazy_refactor` branch (new) |
| `demo_frame_planner.py` | From `frame_planner` branch (new) |
| `FRAME_PLANNER_USAGE.md` | From `frame_planner` branch (new) |
| All other files | From `pe_out_plug_builder` branch (unchanged) |

---

## Deploy order

1. **Push all files together.** `streamlit_app.py`, `suspension/__init__.py`,
   `suspension/aero/__init__.py`, `suspension/tubeframe.py`,
   `powertrain/__init__.py`, and `powertrain/engine.py` are a matched set.
2. **Run `suspension/analytics_hardening.sql` in Supabase** (safe to re-run,
   idempotent). No new SQL views in this release.
3. **Confirm** build stamp in Usage section reads `0.22.0-unified` and
   streamlit runtime reads `>= 1.58.0`.

---

## Lazy-loading design contract (for future contributors)

**Rule:** `import suspension` (or `import powertrain`) must never import
numpy, scipy, plotly, pandas, or any suspension submodule as a side effect.

- Add every new public symbol to `_FROM` in `suspension/__init__.py` (or
  `_SYMBOL_HOME` in `suspension/aero/__init__.py`).
- Add every new submodule to `_SUBMODULES` in the relevant `__init__.py`.
- Add every new public symbol to `__all__`.
- Test with `tests/test_lazy_init.py` and `tests/test_lazy_layer.py`.
- Never use bare `from . import X` at module scope in any `__init__.py` —
  that defeats the lazy contract.

**powertrain** follows the same pattern: add new engine symbols to
`_SYMBOL_HOME` in `powertrain/__init__.py`; add them to `streamlit_app.py`
as `_LazySymbol("powertrain.engine", "YourSymbol")` before using them in a
render function.
