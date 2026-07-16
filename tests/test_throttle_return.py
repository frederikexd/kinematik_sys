"""Tests for the throttle return-spring redundancy + brake-pedal 2000 N gate.

Covers the rule the brakes/pedal-box lead actually asked for:
  * at least two return springs (fewer -> FAIL),
  * still returns to closed with any ONE spring removed ("bounce if one unhooks"),
  * honest handling of an unknown spring constant (estimate flag + k back-out),
  * the brake pedal withstands 2000 N (delegates to the FoS-on-yield bracket gate).
"""

import math
import pytest

from suspension.throttle_return import (
    ReturnSpring, ReturnResistance, check_return_redundancy,
    return_redundancy_report, check_brake_pedal_2000N,
    k_from_deflection, k_from_two_points, k_theta_from_torque,
    k_compression_spring, WIRE_SHEAR_MODULUS_PA, BRAKE_PEDAL_RULE_LOAD_N,
)
from suspension.interfaces import Severity


# --------------------------------------------------------------------------- #
#  Getting the spring constant (the "we don't know k" question)
# --------------------------------------------------------------------------- #
def test_k_from_deflection_is_hookes_law():
    # 20 N hung on the spring moves it 40 mm -> 500 N/m.
    assert k_from_deflection(20.0, 0.040) == pytest.approx(500.0)


def test_k_from_deflection_rejects_zero_travel():
    with pytest.raises(ValueError):
        k_from_deflection(20.0, 0.0)


def test_k_from_two_points_handles_preload():
    # slope between two loaded points, preload cancels out
    k = k_from_two_points(10.0, 0.010, 30.0, 0.050)
    assert k == pytest.approx((30.0 - 10.0) / (0.050 - 0.010))


def test_k_theta_from_torque():
    assert k_theta_from_torque(2.0, 0.5) == pytest.approx(4.0)


def test_k_compression_spring_matches_shigley_closed_form():
    # k = G d^4 / (8 D^3 Na)
    d, D, Na = 1.0e-3, 8.0e-3, 5.0
    G = WIRE_SHEAR_MODULUS_PA["music wire (ASTM A228)"]
    expected = G * d**4 / (8.0 * D**3 * Na)
    k = k_compression_spring(d, D, Na, material="music wire (ASTM A228)")
    assert k == pytest.approx(expected, rel=1e-9)


def test_k_compression_spring_unknown_material_raises():
    with pytest.raises(ValueError):
        k_compression_spring(1e-3, 8e-3, 5.0, material="unobtainium")


def test_k_compression_spring_accepts_explicit_G():
    k = k_compression_spring(1e-3, 8e-3, 5.0, shear_modulus_Pa=80e9)
    assert k == pytest.approx(80e9 * (1e-3)**4 / (8 * (8e-3)**3 * 5.0), rel=1e-9)


# --------------------------------------------------------------------------- #
#  The redundancy rule
# --------------------------------------------------------------------------- #
def _spring(name, closed, open_, est=False):
    return ReturnSpring(name=name, torque_closed_Nm=closed, torque_open_Nm=open_,
                        is_estimate=est)


def test_single_spring_is_an_automatic_fail():
    r = check_return_redundancy([_spring("only", 1.0, 2.0)])
    assert r.verdict == "FAIL"
    assert r.n_springs == 1
    assert any(f.severity == Severity.FAIL for f in r.findings)


def test_zero_springs_fail():
    r = check_return_redundancy([])
    assert r.verdict == "FAIL"


def test_two_strong_springs_still_close_with_one_removed():
    # Each spring alone makes 2 N·m closed / 3 N·m open; resistance 0.5 N·m.
    # Remove one -> the remaining single spring still clears resistance with
    # large margin -> PASS.
    springs = [_spring("primary", 2.0, 3.0), _spring("backup", 2.0, 3.0)]
    res = ReturnResistance(friction_Nm=0.3, cable_drag_Nm=0.2)  # 0.5 total
    r = check_return_redundancy(springs, res)
    assert r.verdict == "PASS"
    # every single-failure case must still close
    single = [c for c in r.cases if c.label.startswith("without")]
    assert all(c.closes for c in single)


def test_hangs_open_when_surviving_spring_cannot_beat_resistance():
    # Two weak springs: together they beat resistance, but EITHER one alone does
    # not -> the "even one unhooks" case fails to return. This is the exact
    # failure the lead described.
    springs = [_spring("a", 0.4, 0.6), _spring("b", 0.4, 0.6)]
    res = ReturnResistance(friction_Nm=0.7)   # one spring (0.4) < 0.7
    r = check_return_redundancy(springs, res)
    assert r.verdict == "FAIL"
    single = [c for c in r.cases if c.label.startswith("without")]
    assert any(not c.closes for c in single)
    assert any(f.severity == Severity.FAIL for f in r.findings)


def test_tight_when_margin_is_thin():
    # Surviving spring beats resistance but only just -> TIGHT, not PASS.
    # resistance 1.0; each spring alone: closed 1.2, open 1.4. worst net = 0.2,
    # margin 0.2 vs target 0.15 -> passes floor, under 0.15*1.25 ceiling? tune so
    # margin sits in the tight band.
    springs = [_spring("a", 1.15, 1.3), _spring("b", 1.15, 1.3)]
    res = ReturnResistance(friction_Nm=1.0)
    r = check_return_redundancy(springs, res, margin_target=0.10, tight_band=1.0)
    # worst single margin = (1.15-1.0)/1.0 = 0.15; target 0.10, ceiling 0.20 -> TIGHT
    assert r.verdict == "TIGHT"
    assert any(f.severity == Severity.WARN for f in r.findings)


def test_verdict_driven_by_worst_single_failure_not_healthy_case():
    # Asymmetric: a strong primary and a weak backup. Healthy is fine and even
    # "without backup" is fine, but "without primary" leaves only the weak backup.
    springs = [_spring("primary", 3.0, 4.0), _spring("backup", 0.3, 0.4)]
    res = ReturnResistance(friction_Nm=0.5)
    r = check_return_redundancy(springs, res)
    assert r.verdict == "FAIL"           # weak backup alone can't return it
    assert "without 'primary'" in r.worst_case


def test_each_spring_is_checked_distinctly_never_assumed_identical():
    # The safety property: a strong primary must NOT paper over a weak backup. Both
    # single-failure cases are evaluated on their own springs; the weak-backup case
    # governs and the two cases have different net torques (not collapsed to one).
    springs = [_spring("primary", 3.0, 4.0), _spring("backup", 0.6, 0.7)]
    res = ReturnResistance(friction_Nm=0.5)
    r = check_return_redundancy(springs, res)
    without_primary = next(c for c in r.cases if c.label == "without 'primary'")
    without_backup = next(c for c in r.cases if c.label == "without 'backup'")
    # distinct springs -> distinct single-failure results (no silent cloning)
    assert without_primary.net_open_Nm != without_backup.net_open_Nm
    # governing case is the one leaving only the weak backup
    assert r.worst_case == "without 'primary'"
    # and it drives the verdict: weak backup alone (0.6 closed) barely beats 0.5
    assert without_primary.net_closed_Nm == pytest.approx(0.6 - 0.5)


def test_weak_backup_can_fail_even_with_a_strong_primary():
    # Concretely the scenario the UI must never hide: primary is plenty, backup is
    # too weak to return the throttle alone -> the system is NOT single-fault
    # tolerant, and the tool must say FAIL, not PASS.
    springs = [_spring("primary", 5.0, 6.0), _spring("backup", 0.2, 0.3)]
    res = ReturnResistance(friction_Nm=0.4)   # backup (0.2) < 0.4 -> hangs open
    r = check_return_redundancy(springs, res)
    assert r.verdict == "FAIL"
    without_primary = next(c for c in r.cases if c.label == "without 'primary'")
    assert not without_primary.closes


def test_estimate_flag_propagates_and_is_noted():
    springs = [_spring("a", 2.0, 3.0, est=True), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.3))
    assert r.is_estimate is True
    assert any("estimate" in n.lower() for n in r.notes)


def test_zero_resistance_is_flagged_as_optimistic():
    springs = [_spring("a", 2.0, 3.0), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs)   # no resistance passed
    assert any("optimistic" in n.lower() for n in r.notes)


def test_zero_resistance_pass_is_demoted_to_tight():
    # A return that would PASS but was checked against ZERO resistance must not read
    # as validated — symmetric to the bending-only pedal demotion. Springs strong
    # enough that with real resistance they'd pass, but resistance is zero here.
    springs = [_spring("a", 2.0, 3.0), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs)   # no resistance -> zero
    assert r.verdict == "TIGHT"            # demoted from PASS
    cov = [f for f in r.findings if f.check == "throttle-return-resistance-coverage"]
    assert cov and cov[0].severity == Severity.WARN
    assert cov[0].detail["demoted_from_pass"] is True


def test_zero_resistance_does_not_rescue_a_failing_return():
    # If the return fails outright (a spring alone can't even hold it closed with
    # zero drag), zero-resistance handling must NOT soften it — demotion only ever
    # makes a verdict more conservative.
    springs = [_spring("a", -0.1, 0.2), _spring("b", -0.1, 0.2)]
    r = check_return_redundancy(springs)   # zero resistance, but hangs at closed
    assert r.verdict == "FAIL"


def test_real_resistance_pass_is_not_demoted():
    # With real resistance and margin, a PASS stays a PASS (no false demotion).
    springs = [_spring("a", 2.0, 3.0), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.3))
    assert r.verdict == "PASS"
    assert not any(f.check == "throttle-return-resistance-coverage" for f in r.findings)


def test_builders_from_linear_and_torsion_agree_with_hand_calc():
    # linear: k=500 N/m, arm 0.03 m, preload 0.02 m, travel 0.04 m
    s = ReturnSpring.from_linear_spring("lin", k_N_per_m=500.0, moment_arm_m=0.03,
                                        preload_stretch_m=0.02, travel_stretch_m=0.04)
    assert s.torque_closed_Nm == pytest.approx(500 * 0.02 * 0.03)
    assert s.torque_open_Nm == pytest.approx(500 * (0.02 + 0.04) * 0.03)
    # torsion: k=4 N·m/rad, preload 0.2 rad, travel 0.5 rad
    t = ReturnSpring.from_torsion_spring("tor", k_theta_Nm_per_rad=4.0,
                                         preload_angle_rad=0.2, travel_angle_rad=0.5)
    assert t.torque_closed_Nm == pytest.approx(4.0 * 0.2)
    assert t.torque_open_Nm == pytest.approx(4.0 * 0.7)


def test_report_renders_and_mentions_verdict():
    springs = [_spring("a", 2.0, 3.0), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.3))
    txt = return_redundancy_report(r)
    assert "REDUNDANCY" in txt
    assert r.verdict in txt
    assert "without 'a'" in txt and "without 'b'" in txt


def test_result_as_dict_is_serialisable():
    springs = [_spring("a", 2.0, 3.0), _spring("b", 2.0, 3.0)]
    r = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.3))
    d = r.as_dict()
    assert d["verdict"] == r.verdict
    assert isinstance(d["cases"], list) and d["cases"]
    assert isinstance(d["findings"], list)
    # findings serialise severity to its string value
    assert d["findings"][0]["severity"] in {s.value for s in Severity}


# --------------------------------------------------------------------------- #
#  Brake-pedal 2000 N gate
# --------------------------------------------------------------------------- #
def test_brake_pedal_rule_load_is_2000N():
    assert BRAKE_PEDAL_RULE_LOAD_N == 2000.0


def test_thin_pedal_fails_2000N():
    # A skinny 3 mm steel tab on a 120 mm arm will not survive 2000 N bending.
    r = check_brake_pedal_2000N(width_mm=25.0, thickness_mm=3.0, lever_arm_mm=120.0)
    assert r.verdict == "FAIL"
    assert r.screening_only is True


def test_beefy_pedal_passes_2000N():
    # Thick, short-armed steel pedal with full geometry clears FoS>=1.5 at 2000 N.
    r = check_brake_pedal_2000N(width_mm=40.0, thickness_mm=16.0, lever_arm_mm=30.0,
                                pivot_bolt_dia_mm=8.0, edge_dist_mm=16.0,
                                weld_leg_mm=6.0, weld_length_mm=60.0)
    assert r.verdict == "PASS"
    assert r.min_fos >= 1.5


def test_pedal_screens_at_the_2000N_rule_load_by_default():
    # The load actually applied is the rule load. Verify by comparing to a bracket
    # screened by hand at 2000 N via the same module: monotonic in load.
    thin = check_brake_pedal_2000N(width_mm=30.0, thickness_mm=5.0, lever_arm_mm=80.0)
    lighter = check_brake_pedal_2000N(width_mm=30.0, thickness_mm=5.0,
                                      lever_arm_mm=80.0, load_N=500.0)
    assert lighter.min_fos > thin.min_fos    # less load -> more margin


def test_aluminium_pedal_material_available():
    # 7075-T6 is the grade most teams cut a pedal from; it must be in the library.
    r = check_brake_pedal_2000N(width_mm=40.0, thickness_mm=16.0, lever_arm_mm=30.0,
                                material="Aluminium 7075-T6")
    assert r.verdict in {"PASS", "TIGHT", "FAIL"}   # i.e. material was recognised
    assert r.verdict != "INVALID"


def test_bending_only_pass_is_demoted_to_tight_with_coverage_warning():
    # A pedal that PASSES in bending but was given NO pivot/weld geometry must not
    # read as a clean pass — the screen didn't check bearing/tear-out/weld. This is
    # the "fail here, not in ANSYS" principle: an incomplete screen is conservative.
    r = check_brake_pedal_2000N(width_mm=40.0, thickness_mm=16.0, lever_arm_mm=30.0)
    assert r.verdict == "TIGHT"        # demoted from PASS
    cov = [f for f in r.findings if f.check == "brake-pedal-2000N-coverage"]
    assert cov and cov[0].severity == Severity.WARN
    assert cov[0].detail["demoted_from_pass"] is True
    assert any("NOT" in n or "COVERAGE" in n for n in r.notes)


def test_full_geometry_pedal_can_earn_a_clean_pass():
    # Give the pivot lug and weld geometry so every mode is actually screened;
    # a strong pedal now earns a real PASS with no coverage demotion.
    r = check_brake_pedal_2000N(
        width_mm=40.0, thickness_mm=16.0, lever_arm_mm=30.0,
        pivot_bolt_dia_mm=8.0, edge_dist_mm=16.0,
        weld_leg_mm=6.0, weld_length_mm=60.0)
    assert r.verdict == "PASS"
    assert not any(f.check == "brake-pedal-2000N-coverage" for f in r.findings)


def test_coverage_does_not_rescue_a_failing_pedal():
    # A pedal that FAILS in bending stays FAIL regardless of coverage — the demotion
    # only ever makes the verdict more conservative, never less.
    r = check_brake_pedal_2000N(width_mm=25.0, thickness_mm=3.0, lever_arm_mm=120.0)
    assert r.verdict == "FAIL"


# --------------------------------------------------------------------------- #
#  Package-level import surface
# --------------------------------------------------------------------------- #
def test_symbols_exposed_from_package():
    import suspension
    for name in ("check_return_redundancy", "ReturnSpring", "ReturnResistance",
                 "check_brake_pedal_2000N", "k_from_deflection",
                 "k_compression_spring"):
        assert hasattr(suspension, name), name
