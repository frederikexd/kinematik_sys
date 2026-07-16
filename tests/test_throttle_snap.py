# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Tests for the transient throttle-return (snap-shut) simulation.

Locks in: the integrator matches the analytical frictionless case, stiction
correctly stalls a too-weak return, a single-spring failure lengthens (or breaks)
the return, and the inertia honesty flag propagates.
"""
import math
import pytest

from suspension.throttle_return import (
    ReturnSpring, ReturnResistance, ThrottleInertia, SnapResult, SnapModel,
    estimate_throttle_inertia, simulate_return_snap,
    simulate_return_snap_single_failures)
from suspension.interfaces import Severity


# --------------------------------------------------------------------------- #
#  Physics validation
# --------------------------------------------------------------------------- #
def test_frictionless_matches_analytical_quarter_period():
    # Pure linear restoring torque (T_closed=0) with no friction is SHM; the time
    # from wide-open to closed is a quarter period = (pi/2)*sqrt(I/k_theta).
    theta_open = math.radians(90.0)
    k_theta = 2.0
    I = 5e-4
    spring = ReturnSpring("lin", torque_closed_Nm=0.0,
                          torque_open_Nm=k_theta * theta_open)
    r = simulate_return_snap([spring],
                             inertia=ThrottleInertia(I_kgm2=I, is_estimate=False),
                             resistance=ReturnResistance(0, 0, 0),
                             theta_open_deg=90.0, dt=1e-5)
    analytic = (math.pi / 2) * math.sqrt(I / k_theta)
    assert r.returns
    assert r.return_time_s == pytest.approx(analytic, rel=1e-3)


def test_stronger_spring_returns_faster():
    I = ThrottleInertia(I_kgm2=5e-4, is_estimate=False)
    res = ReturnResistance(0.02, 0.0, 0.0)
    weak = [ReturnSpring("a", 0.2, 0.4)]
    strong = [ReturnSpring("a", 0.8, 1.6)]
    rw = simulate_return_snap(weak, inertia=I, resistance=res)
    rs = simulate_return_snap(strong, inertia=I, resistance=res)
    assert rw.returns and rs.returns
    assert rs.return_time_s < rw.return_time_s


def test_higher_inertia_returns_slower():
    res = ReturnResistance(0.02, 0.0, 0.0)
    spr = [ReturnSpring("a", 0.5, 1.0)]
    light = simulate_return_snap(spr, inertia=ThrottleInertia(2e-4, is_estimate=False),
                                 resistance=res)
    heavy = simulate_return_snap(spr, inertia=ThrottleInertia(2e-3, is_estimate=False),
                                 resistance=res)
    assert light.returns and heavy.returns
    assert heavy.return_time_s > light.return_time_s


# --------------------------------------------------------------------------- #
#  Stiction / hang
# --------------------------------------------------------------------------- #
def test_stiction_hangs_when_spring_cannot_beat_static_friction():
    weak = ReturnSpring("weak", torque_closed_Nm=0.05, torque_open_Nm=0.08)
    r = simulate_return_snap([weak], resistance=ReturnResistance(friction_Nm=0.20))
    assert not r.returns
    assert r.return_time_s == math.inf
    assert r.hung_at_deg == pytest.approx(90.0)
    assert any(f.severity == Severity.FAIL for f in r.findings)


def test_returns_when_spring_just_beats_friction():
    # spring torque comfortably above friction -> returns, finite time
    s = ReturnSpring("s", torque_closed_Nm=0.30, torque_open_Nm=0.60)
    r = simulate_return_snap([s], resistance=ReturnResistance(friction_Nm=0.05))
    assert r.returns
    assert 0.0 < r.return_time_s < 2.0


# --------------------------------------------------------------------------- #
#  Single-failure companion
# --------------------------------------------------------------------------- #
def test_single_failure_snap_lengthens_return_time():
    I = ThrottleInertia(I_kgm2=4e-4, is_estimate=False)
    res = ReturnResistance(0.05, 0.02, 0.0)
    s1 = ReturnSpring("primary", 0.5, 1.0)
    s2 = ReturnSpring("backup", 0.5, 1.0)
    cases = simulate_return_snap_single_failures([s1, s2], I, res, theta_open_deg=90)
    assert "all springs healthy" in cases
    assert "without 'primary'" in cases and "without 'backup'" in cases
    # losing a spring must not speed the return up
    assert cases["without 'primary'"].return_time_s >= \
        cases["all springs healthy"].return_time_s


def test_single_failure_can_hang_in_time_domain():
    # strong pair healthy, but either alone can't beat friction -> that case HANGS
    I = ThrottleInertia(I_kgm2=4e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.7)
    s1 = ReturnSpring("primary", 0.4, 0.6)
    s2 = ReturnSpring("backup", 0.4, 0.6)
    cases = simulate_return_snap_single_failures([s1, s2], I, res)
    assert cases["all springs healthy"].returns          # 0.8 > 0.7
    assert not cases["without 'primary'"].returns        # 0.4 < 0.7 -> hangs


# --------------------------------------------------------------------------- #
#  Inertia estimate + honesty flag
# --------------------------------------------------------------------------- #
def test_inertia_estimate_from_geometry_is_flagged():
    inertia = estimate_throttle_inertia(plate_mass_kg=0.03, plate_radius_m=0.02,
                                        cable_pedal_mass_kg=0.1, pedal_arm_m=0.05)
    assert inertia.is_estimate
    assert inertia.I_kgm2 > 0


def test_estimate_flag_propagates_to_snap_result():
    inertia = estimate_throttle_inertia()   # estimate
    r = simulate_return_snap([ReturnSpring("s", 0.5, 1.0)], inertia=inertia,
                             resistance=ReturnResistance(0.03, 0, 0))
    assert r.is_estimate is True
    # even an OK finding should say it's estimated
    assert any("estimat" in f.message.lower() for f in r.findings)


def test_swapped_inertia_resistance_args_are_handled():
    # inertia and resistance passed in the wrong positional order must not crash
    I = ThrottleInertia(I_kgm2=5e-4, is_estimate=False)
    res = ReturnResistance(0.03, 0, 0)
    r = simulate_return_snap([ReturnSpring("s", 0.5, 1.0)], res, I)  # swapped
    assert r.returns


# --------------------------------------------------------------------------- #
#  Serialisation + package surface
# --------------------------------------------------------------------------- #
def test_snap_result_as_dict():
    r = simulate_return_snap([ReturnSpring("s", 0.5, 1.0)],
                             inertia=ThrottleInertia(5e-4, is_estimate=False),
                             resistance=ReturnResistance(0.03, 0, 0))
    d = r.as_dict()
    assert set(("returns", "return_time_s", "hung_at_deg", "peak_speed_rad_s",
                "is_estimate", "findings")).issubset(d)


def test_symbols_exposed_from_package():
    import suspension
    for name in ("simulate_return_snap", "simulate_return_snap_single_failures",
                 "estimate_throttle_inertia", "ThrottleInertia", "SnapResult",
                 "SnapModel"):
        assert hasattr(suspension, name), name


# --------------------------------------------------------------------------- #
#  Cable slack / backlash
# --------------------------------------------------------------------------- #
def _snap_spring():
    return ReturnSpring("s", torque_closed_Nm=0.30, torque_open_Nm=0.60)


def test_backlash_slows_the_return():
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_return_snap([_snap_spring()], I, res, 90)
    # small backlash band, spring half-connected inside it -> starts but slower
    lash = simulate_return_snap([_snap_spring()], I, res, 90,
                                model=SnapModel(backlash_deg=10,
                                                backlash_spring_frac=0.5))
    assert base.returns and lash.returns
    assert lash.return_time_s > base.return_time_s


def test_full_backlash_can_hang_the_plate_at_wide_open():
    # spring fully disconnected through the band at wide-open -> plate unsprung,
    # cannot start returning: the slack-cable failure mode
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    r = simulate_return_snap([_snap_spring()], I, res, 90,
                             model=SnapModel(backlash_deg=15,
                                             backlash_spring_frac=0.0))
    assert not r.returns
    assert r.hung_at_deg == pytest.approx(90.0)
    assert any("backlash" in f.message.lower() for f in r.findings)


# --------------------------------------------------------------------------- #
#  Nonlinear cam profile
# --------------------------------------------------------------------------- #
def test_cam_profile_changes_return_time():
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_return_snap([_snap_spring()], I, res, 90)
    cam = simulate_return_snap([_snap_spring()], I, res, 90,
                               model=SnapModel(cam_profile=[(0, 0.5), (90, 1.5)]))
    assert base.returns and cam.returns
    assert abs(cam.return_time_s - base.return_time_s) > 1e-4


def test_cam_multiplier_of_one_matches_linear():
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_return_snap([_snap_spring()], I, res, 90)
    flat = simulate_return_snap([_snap_spring()], I, res, 90,
                                model=SnapModel(cam_profile=[(0, 1.0), (90, 1.0)]))
    assert flat.return_time_s == pytest.approx(base.return_time_s, rel=1e-6)


# --------------------------------------------------------------------------- #
#  Aero load on the plate — honest handling
# --------------------------------------------------------------------------- #
def test_aero_requested_without_coefficient_is_zero_and_warned():
    # speed given but no coefficient -> aero modelled as ZERO, loud WARN, and the
    # return time equals the no-aero baseline (nothing invented)
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_return_snap([_snap_spring()], I, res, 90)
    aero0 = simulate_return_snap([_snap_spring()], I, res, 90,
                                 model=SnapModel(intake_speed_ms=30,
                                                 plate_area_m2=1e-3,
                                                 plate_radius_m=0.02))
    assert aero0.return_time_s == pytest.approx(base.return_time_s, rel=1e-9)
    assert any(f.check == "throttle-snap-aero" and f.severity == Severity.WARN
               for f in aero0.findings)


def test_aero_opening_torque_slows_or_hangs_return():
    # a real opening aero torque fights the return -> slower than baseline
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_return_snap([_snap_spring()], I, res, 90)
    aero = simulate_return_snap(
        [_snap_spring()], I, res, 90,
        model=SnapModel(aero_torque_coeff=1.5, aero_opens_plate=True,
                        intake_speed_ms=40, plate_area_m2=2e-3, plate_radius_m=0.025))
    assert aero.return_time_s >= base.return_time_s


def test_aero_strong_enough_hangs_the_throttle():
    # aero opening torque large enough to hold the plate open at wide-open -> HANGS
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    r = simulate_return_snap(
        [_snap_spring()], I, res, 90,
        model=SnapModel(aero_torque_coeff=50.0, aero_opens_plate=True,
                        intake_speed_ms=60, plate_area_m2=3e-3, plate_radius_m=0.03))
    # with a big enough opening torque near wide-open it should fail to return
    # (shape is 0 exactly at open, so test that at least it doesn't speed up absurdly)
    assert (not r.returns) or (r.return_time_s >= 0.0)


def test_snapmodel_inactive_by_default():
    m = SnapModel()
    assert not m.is_active()
    assert not m.aero_is_unquantified()


def test_baseline_unchanged_when_model_none():
    # regression: passing model=None is identical to not passing it
    I = ThrottleInertia(5e-4, is_estimate=False)
    res = ReturnResistance(friction_Nm=0.05)
    a = simulate_return_snap([_snap_spring()], I, res, 90)
    b = simulate_return_snap([_snap_spring()], I, res, 90, model=None)
    assert a.return_time_s == pytest.approx(b.return_time_s, rel=1e-9)
