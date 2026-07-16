"""Tests for suspension.pt_integration — the powertrain integration layer."""

import math
import numpy as np
import pytest

from suspension import pt_integration as pti
from suspension.laptime import MotorMap


def _map():
    # Representative FSAE-EV motor: 230 N·m, 80 kW, 6000 rpm redline.
    return MotorMap.from_peak(230.0, 80.0, 6000.0, final_drive=3.5,
                              wheel_radius_m=0.20)


# --------------------------- gear solver --------------------------------- #
def test_gear_sweep_returns_best_and_orders_metrics():
    solver = pti.GearRatioSolver(_map(), mass_kg=300.0, wheel_r_m=0.20)
    res = solver.sweep([2.5, 3.0, 3.5, 4.0, 4.5], pti.GearObjective.BALANCED)
    assert res.best is not None
    assert len(res.candidates) == 5
    # taller ratio -> higher top speed (monotonic-ish on redline speed)
    by_fd = {c.final_drive: c for c in res.candidates}
    assert by_fd[2.5].redline_speed_kmh > by_fd[4.5].redline_speed_kmh


def test_accel_objective_prefers_shorter_or_equal_ratio_than_topspeed():
    solver = pti.GearRatioSolver(_map(), mass_kg=300.0, wheel_r_m=0.20)
    ratios = [2.5, 3.0, 3.5, 4.0, 4.5]
    accel = solver.sweep(ratios, pti.GearObjective.ACCEL).best
    top = solver.sweep(ratios, pti.GearObjective.TOPSPEED).best
    # a shorter (numerically larger) final drive helps accel; top speed wants taller
    assert accel.final_drive >= top.final_drive


def test_solver_requires_motor_map():
    with pytest.raises(ValueError):
        pti.GearRatioSolver(None, mass_kg=300, wheel_r_m=0.2)


def test_redline_limit_flag_set_for_short_ratio():
    # A low-drag car on a very short ratio runs out of redline before drag stops
    # it. Use a tiny cda so the power/drag balance can't be the limiter.
    solver = pti.GearRatioSolver(_map(), mass_kg=300.0, wheel_r_m=0.20, cda=0.2)
    res = solver.sweep([8.0], pti.GearObjective.BALANCED)
    assert res.candidates[0].redline_limited


# --------------------------- sprocket ------------------------------------ #
def test_sprocket_is_a_reduction_and_tension_positive():
    sd = pti.sprocket_design(3.5, 230.0, "#35 (3/8\")", motor_sprocket_teeth=14)
    assert sd.driven_sprocket_teeth > sd.motor_sprocket_teeth
    assert abs(sd.actual_ratio - 49 / 14) < 1e-6  # 14*3.5 = 49
    assert sd.chain_tension_n > 0
    assert sd.tooth_force_n == sd.chain_tension_n


def test_sprocket_pitch_diameter_formula():
    # D = p / sin(pi/N); check against a hand calc for #35, 49T
    p = 9.525
    d = pti._pitch_diameter_mm(49, p)
    assert math.isclose(d, p / math.sin(math.pi / 49), rel_tol=1e-9)


def test_higher_ratio_more_output_torque():
    t1 = pti.driveline_peak_torque_nm(3.0, 230.0)
    t2 = pti.driveline_peak_torque_nm(4.0, 230.0)
    assert t2 > t1


# --------------------------- fan / cooling ------------------------------- #
def test_spal_curve_monotonic_decreasing_pressure_with_flow():
    fan = pti.FanCurve.spal_default()
    # higher flow -> lower static pressure for an axial fan
    assert fan.pressure_at(0.0) > fan.pressure_at(700.0)
    assert fan.pressure_at(700.0) >= 0.0


def test_operating_point_within_fan_range():
    fan = pti.FanCurve.spal_default()
    k = pti.system_k_from_point(400.0, 150.0)   # a measured rig point
    op = pti.cooling_operating_point(fan, k, heat_to_reject_w=2000.0)
    assert fan.flow_m3h.min() <= op.flow_m3h <= fan.flow_m3h.max()
    # the operating dp should be close to k*Q^2 at the solved flow
    assert math.isclose(op.static_pressure_pa, k * op.flow_m3h ** 2, rel_tol=0.05)


def test_under_cooling_flagged():
    fan = pti.FanCurve.spal_default()
    k = pti.system_k_from_point(200.0, 250.0)   # very restrictive loop
    op = pti.cooling_operating_point(fan, k, heat_to_reject_w=8000.0,
                                     air_delta_t_c=20.0)
    assert not op.adequate
    assert op.margin_w < 0
    assert any("under-cooled" in w for w in op.warnings)


def test_system_k_back_calc():
    k = pti.system_k_from_point(400.0, 160.0)
    assert math.isclose(k * 400.0 ** 2, 160.0, rel_tol=1e-9)


# --------------------------- spec sheet / heat --------------------------- #
def test_spec_sheet_has_core_rows():
    rows = pti.powertrain_spec_sheet(
        architecture="Single motor + diff", power_kw=80, peak_torque_nm=230,
        hv_voltage_v=400, pack_kwh=6.5, final_drive=3.5,
        motor_teeth=14, driven_teeth=49, chain_tension_n=3200,
        driveline_torque_nm=780, motor_mass_kg=22, heat_reject_w=2600,
        cooling_flow_cms=0.11)
    params = {r["Parameter"] for r in rows}
    assert "Peak motor torque" in params
    assert "Final drive ratio" in params
    assert "Chain tension @ peak torque" in params


def test_motor_heat_estimate_scales_with_power():
    h1 = pti.estimate_motor_heat_w(40.0)
    h2 = pti.estimate_motor_heat_w(80.0)
    assert h2 > h1 > 0


# --------------------------- DFMEA auto-rows ----------------------------- #
def test_dfmea_rows_match_columns_and_have_rpn():
    from suspension import dfmea as D
    rows = pti.dfmea_rows_from_analysis(
        sprocket=pti.sprocket_design(3.5, 230.0),
        cooling=pti.cooling_operating_point(
            pti.FanCurve.spal_default(),
            pti.system_k_from_point(200.0, 250.0), heat_to_reject_w=8000.0),
        output_torque_nm=780.0, mount_load_n=5200.0, owner="Erick")
    assert len(rows) == 3
    cols = set(D.COLUMNS)
    for r in rows:
        assert set(r.keys()) == cols
        assert r["RPN"] == r["Severity"] * r["Occurrence"] * r["Detection"]
        assert r["Owner"] == "Erick"


def test_dfmea_undercooled_raises_severity():
    under = pti.cooling_operating_point(
        pti.FanCurve.spal_default(), pti.system_k_from_point(200.0, 280.0),
        heat_to_reject_w=9000.0)
    ok = pti.cooling_operating_point(
        pti.FanCurve.spal_default(), pti.system_k_from_point(550.0, 60.0),
        heat_to_reject_w=500.0)
    r_under = pti.dfmea_rows_from_analysis(cooling=under)[0]
    r_ok = pti.dfmea_rows_from_analysis(cooling=ok)[0]
    assert r_under["Severity"] >= r_ok["Severity"]


def test_dfmea_empty_when_no_inputs():
    assert pti.dfmea_rows_from_analysis() == []


# --------------------------- motor envelope (the Discord confusion) ------ #
def test_base_speed_below_redline_and_power_holds_to_redline():
    env = pti.motor_envelope(230.0, 80.0, 7000.0)
    # base speed must be well below redline (that's the whole point)
    assert env.base_speed_rpm < env.redline_rpm
    # power at redline should still be ~peak (constant-power region), not zero
    assert env.power_at_redline_kw() > 0.9 * env.peak_power_kw


def test_power_cap_does_not_cap_rpm():
    # capping power at 80kW must NOT reduce the redline — rpm range is unchanged
    env = pti.motor_envelope(230.0, 80.0, 9000.0)
    assert env.rpm.max() == 9000.0
    assert "does NOT cap rpm" in env.explanation()


def test_continuous_never_exceeds_peak():
    env = pti.motor_envelope(230.0, 80.0, 7000.0, continuous_frac=0.7)
    assert env.continuous_power_kw <= env.peak_power_kw
    # even if someone passes >1.0, it's clamped
    env2 = pti.motor_envelope(230.0, 80.0, 7000.0, continuous_frac=2.0)
    assert env2.continuous_power_kw <= env2.peak_power_kw


def test_fsae_cap_clamps_over_cap_declaration():
    env = pti.motor_envelope(300.0, 120.0, 7000.0)   # 120 kW declared, over the cap
    assert env.over_cap
    assert env.peak_power_kw == pytest.approx(80.0)   # clamped to the rule cap
    assert max(env.power_kw) <= 80.0 + 1e-6


def test_myth_checks_flag_both_confusions_as_myths():
    env = pti.motor_envelope(230.0, 80.0, 7000.0)
    checks = pti.power_rpm_myth_checks(env, gear_final_drive=3.5)
    verdicts = [c.verdict for c in checks]
    assert verdicts.count("myth") >= 2
    text = " ".join(c.correction for c in checks)
    assert "redline" in text.lower()
    assert "continuous" in text.lower()
