# KinematiK × SysBridge Integration

## What this is

KinematiK now has a **⬡ SYSBRIDGE RISK** tab that runs the SysBridge risk engine live against your current suspension design. Every time you change hardpoints or vehicle parameters, the risk score updates automatically.

## How the mapping works

SysBridge scores designs across nine risk components (R1–R9). KinematiK maps its physics outputs onto those components as follows:

| R-code | Label | KinematiK source |
|--------|-------|-----------------|
| R1 | Recall / event severity | Out-of-tolerance correlation channels (Validation tab) |
| R2 | FMEA criticality (proxy RPN) | Camber gain, bump steer, solver convergence quality |
| R3 | Detection gap | Fraction of travel sweep that didn't converge |
| R4 | Remaining life | User-entered service age vs. 3-season FSAE life |
| R5 | Stability / amplification | Camber gain magnitude → sensitivity amplifier |
| R6 | Completeness | Geometry variable coverage (sweep convergence fraction) |
| R7 | Discipline / jurisdiction | Fixed: "motorsport suspension" / user-selected |
| R8 | QMS open issues | Open NCRs (user-entered) + OOT channels |
| R9 | Physics model coverage | KinematiK model name + max member load fraction |

## Files added

| File | Purpose |
|------|---------|
| `sysbridge_engine.py` | The SysBridge scoring engine (pure stdlib, no extra deps) |
| `sysbridge_kinematik.py` | KinematiK ↔ SysBridge bridge: mapping + pipeline |

## Running

No change to how you run KinematiK:

```bash
streamlit run app.py
```

The SysBridge tab appears as the last tab: **⬡ SYSBRIDGE RISK**.

## What the score means

| Score | Gate | Meaning |
|-------|------|---------|
| 0–39 | PASS | No critical failures; continue normal design cycle |
| 40–69 | CONDITIONAL | No critical failures, but high-severity findings need resolution before full release |
| ≥ 70 | REJECT | Critical failure present, or overall score too high |
| ≥ 70 + active interactions | HOLD | Same as REJECT, but amplification makes the design worse than the score suggests |

## Interpreting for FSAE

- **R5 (stability) is the most directly suspension-relevant score.** High camber gain (> 3.5 °/10mm) or high bump steer signals a design that amplifies disturbances — small hardpoint tolerances or compliance-steer effects become large on-track.
- **R3 (detection gap) is a solver quality signal.** If more than 10% of your sweep didn't converge, the geometry has states the model can't inspect — fix the hardpoints before trusting the risk number.
- **R6 (completeness) improves as your sweep converges fully.** A fully converged 41-point sweep scores 30/30 effective variables.
- **Run a Validation correlation first** (Validation tab) and the R1/R2/R8 scores become data-driven rather than defaulting to zero.
