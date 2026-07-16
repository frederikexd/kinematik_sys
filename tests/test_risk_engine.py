# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Tests for suspension/risk_engine.py — matrix triggering, severity elevation,
cross-subsystem occurrence propagation, live RPN consistency with dfmea.py, and
the slotted-hole torque/preload calculator. Run: python tests/test_risk_engine.py"""

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(name):
    # Import through the real (lazy) package so pytest and standalone runs see
    # the same module objects — no stub `suspension` in sys.modules.
    return importlib.import_module(f"suspension.{name}")


DF = _load("dfmea")
BJ = _load("bolted_joint")
RE = _load("risk_engine")

_PASS, _FAIL = [], []


def check(name, cond):
    (_PASS if cond else _FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


eng = RE.RiskEngine()

# --- compliant car: nothing triggers ---------------------------------------- #
ok = eng.evaluate(RE.readings(
    ("chassis", "node_fos", 2.1), ("chassis", "bracket_fos", 1.8),
    ("brakes", "caliper_bracket_fos", 2.2), ("powertrain", "shaft_fos", 1.9),
    ("cooling", "manifold_dp_kpa:radiator_in", 12.0),
    ("cooling", "pump_inlet_margin_kpa", 40.0)))
check("compliant readings trigger nothing", len(ok.active()) == 0)
check("compliant summary max_rpn 0", ok.summary()["max_rpn"] == 0)

# --- FoS breach: severity elevates with deficit, RPN = dfmea math ----------- #
bad = eng.evaluate(RE.readings(("brakes", "caliper_bracket_fos", 1.2)))
row = bad.worst()
check("FoS breach triggers", row is not None and row.triggered)
check("severity elevated above base", row.severity > 9 - 1 and row.severity >= 9)
check("rpn equals dfmea compute_rpn",
      row.rpn == DF.compute_rpn(row.severity, row.occurrence, row.detection))
deep = eng.evaluate(RE.readings(("brakes", "caliper_bracket_fos", 0.7))).worst()
check("deeper deficit ⇒ severity monotone", deep.severity >= row.severity)
check("deeper deficit ⇒ occurrence monotone", deep.occurrence >= row.occurrence)
check("band consistent with dfmea",
      deep.band == DF.classify_risk(deep.severity, deep.rpn).value)

# --- manifold localized ΔP: per-segment rows + severity elevation ----------- #
man = eng.evaluate(RE.manifold_readings(
    {"motor_branch": 52.0, "inverter_branch": 10.0}, inlet_abs_kpa=25.0))
act = man.active()
check("only the bad segment triggers",
      len([r for r in act if r.rule_id == "COOL-MANIFOLD-DP"]) == 1)
seg = [r for r in act if r.rule_id == "COOL-MANIFOLD-DP"][0]
check("segment channel preserved", seg.channel.endswith("motor_branch"))
check("dangerous ΔP elevates severity ≥ 9", seg.severity >= 9)
cav = [r for r in act if r.rule_id == "COOL-CAVITATION"]
check("thin vapor margin triggers cavitation rule", len(cav) == 1)

# --- propagation: chassis FoS breach bumps downstream brake occurrence ------ #
solo = eng.evaluate(RE.readings(("brakes", "caliper_bracket_fos", 2.2)))
brk_solo = [r for r in solo.risks if r.rule_id == "BRK-MOUNT-SHEAR"][0]
combo = eng.evaluate(RE.readings(("brakes", "caliper_bracket_fos", 2.2),
                                 ("chassis", "bracket_fos", 1.0)))
brk_combo = [r for r in combo.risks if r.rule_id == "BRK-MOUNT-SHEAR"][0]
check("propagation bumps downstream occurrence",
      brk_combo.occurrence == min(10, brk_solo.occurrence + 2))
check("propagation records source rule", brk_combo.propagated_from == ["CHS-BRACKET-YIELD"])
check("propagation never fabricates a breach", brk_combo.triggered is False)
check("propagated rpn recomputed", brk_combo.rpn ==
      DF.compute_rpn(brk_combo.severity, brk_combo.occurrence, brk_combo.detection))

# --- determinism + DFMEA record shape --------------------------------------- #
r1 = eng.evaluate(RE.readings(("chassis", "node_fos", 1.1))).as_records()
r2 = eng.evaluate(RE.readings(("chassis", "node_fos", 1.1))).as_records()
check("evaluation is deterministic", r1 == r2)
check("records use dfmea columns", set(r1[0]) == set(DF.COLUMNS))
check("unmatched readings surfaced", eng.evaluate(
    RE.readings(("aero", "wing_fos", 0.5))).summary()["unmatched_readings"]
    == ["aero.wing_fos"])

# --- slotted-hole calculator ------------------------------------------------- #
j = RE.SlottedHoleJoint(fastener=BJ.Fastener(grade="10.9", nominal_d_mm=6.0),
                        slot_width_mm=6.6, slot_length_mm=20.0, washer_od_mm=18.0,
                        bearing_allow_MPa=300.0)
res = RE.analyze_slotted_joint(j, assembly_torque_Nm=9.0)
check("slot reduces bearing area", res.bearing_area_slotted_mm2
      < res.bearing_area_full_mm2)
check("area ratio in (0,1)", 0.0 < res.area_ratio < 1.0)
check("K adjusts with contact geometry", abs(res.K_eff - res.K_nominal) > 1e-4)
# clamp at the same wrench setting follows F = T/(K_eff·d)
check("clamp force from K_eff", abs(res.F_clamp_at_torque_N
      - 9.0e3 / (res.K_eff * 6.0)) < 1.0)
check("bearing stress reported", res.bearing_stress_MPa is not None
      and abs(res.bearing_stress_MPa
              - res.F_clamp_at_torque_N / res.bearing_area_slotted_mm2) < 0.5)
# no-slot degenerate geometry ⇒ K_eff → K_nominal
tiny = RE.analyze_slotted_joint(RE.SlottedHoleJoint(
    fastener=BJ.Fastener(grade="10.9", nominal_d_mm=6.0),
    slot_width_mm=6.6, slot_length_mm=6.6, washer_od_mm=18.0))
check("degenerate slot ≈ round hole K", abs(tiny.K_eff - tiny.K_nominal) < 0.004)
# bigger cutout ⇒ lower bearing cap
long_slot = RE.analyze_slotted_joint(RE.SlottedHoleJoint(
    fastener=BJ.Fastener(grade="10.9", nominal_d_mm=6.0),
    slot_width_mm=6.6, slot_length_mm=30.0, washer_od_mm=18.0))
check("longer slot ⇒ lower bearing cap",
      long_slot.F_bearing_cap_N < tiny.F_bearing_cap_N)
soft = RE.analyze_slotted_joint(RE.SlottedHoleJoint(
    fastener=BJ.Fastener(grade="12.9", nominal_d_mm=6.0),
    slot_width_mm=6.6, slot_length_mm=20.0, washer_od_mm=13.0,
    bearing_allow_MPa=120.0))
check("soft tab bearing-caps the preload", soft.bearing_capped
      and soft.F_target_N <= soft.F_bearing_cap_N + 0.6)
check("offset marks estimate", RE.analyze_slotted_joint(RE.SlottedHoleJoint(
    fastener=BJ.Fastener(grade="10.9", nominal_d_mm=6.0), slot_width_mm=6.6,
    slot_length_mm=20.0, slot_offset_mm=5.0, washer_od_mm=18.0)).is_estimate)
# feeds the risk matrix
fos_rd = res.pedal_tab_fos_reading(slip_demand_N=res.F_clamp_at_torque_N * 0.35 * 2)
check("pedal-tab reading channel", fos_rd.channel == "pedal_tab_joint_fos")
check("under-clamped tab triggers matrix",
      eng.evaluate([fos_rd]).active()[0].rule_id == "BRK-PEDALTAB-SLIP")
try:
    RE.analyze_slotted_joint(RE.SlottedHoleJoint(slot_width_mm=5.0))
    check("slot narrower than bolt rejected", False)
except ValueError:
    check("slot narrower than bolt rejected", True)

print(f"\n{len(_PASS)} passed, {len(_FAIL)} failed")


# --- pytest bridge: expose every module-level check as a test case ---------- #
import pytest  # noqa: E402


@pytest.mark.parametrize("name", _PASS + _FAIL)
def test_check(name):
    assert name not in _FAIL, f"check failed: {name}"


if __name__ == "__main__":
    sys.exit(1 if _FAIL else 0)
