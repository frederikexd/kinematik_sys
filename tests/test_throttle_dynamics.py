# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Tests for coupled throttle-plate + manifold-pressure dynamics and the plate-
flutter screen — including the honesty guarantees (aero derivatives are never
invented; flutter is always labelled a screen)."""
import math
import pytest

from suspension.throttle_dynamics import (
    compressible_mass_flow, throttle_flow_area, R_AIR, GAMMA,
    ManifoldParams, FlutterParams, CoupledResult, FlutterResult,
    simulate_coupled_return, screen_plate_flutter)
from suspension.throttle_return import (
    ThrottleInertia, ReturnSpring, ReturnResistance)
from suspension.interfaces import Severity


# --------------------------------------------------------------------------- #
#  Compressible flow — validated against textbook limits
# --------------------------------------------------------------------------- #
def test_critical_pressure_ratio_is_textbook():
    crit = (2 / (GAMMA + 1)) ** (GAMMA / (GAMMA - 1))
    assert crit == pytest.approx(0.5283, abs=1e-3)


def test_flow_chokes_below_critical_ratio():
    A, pup, T = 1e-3, 101325.0, 298.0
    crit = (2 / (GAMMA + 1)) ** (GAMMA / (GAMMA - 1))
    at_crit = compressible_mass_flow(A, pup, pup * crit, T)
    deeper = compressible_mass_flow(A, pup, pup * 0.15, T)
    assert at_crit == pytest.approx(deeper, rel=1e-9)   # choked = constant


def test_no_reverse_flow():
    assert compressible_mass_flow(1e-3, 101325, 101325 * 1.1, 298) == 0.0


def test_more_area_more_flow():
    a = compressible_mass_flow(1e-3, 101325, 60000, 298)
    b = compressible_mass_flow(2e-3, 101325, 60000, 298)
    assert b > a


def test_flow_area_bounds_and_monotonic():
    assert throttle_flow_area(0.0, 1.0) == pytest.approx(0.02, abs=1e-6)   # leak
    assert throttle_flow_area(math.radians(90), 1.0) == pytest.approx(1.0, abs=1e-6)
    assert (throttle_flow_area(math.radians(30), 1.0)
            < throttle_flow_area(math.radians(60), 1.0)
            < throttle_flow_area(math.radians(90), 1.0))


# --------------------------------------------------------------------------- #
#  Coupled plate + manifold return
# --------------------------------------------------------------------------- #
def _spring():
    return ReturnSpring.from_linear_spring("p", k_N_per_m=600, moment_arm_m=0.03,
                                           preload_stretch_m=0.02, travel_stretch_m=0.05)


def test_coupled_return_closes_and_tracks_vacuum():
    I = ThrottleInertia(5e-4, is_estimate=False)
    mp = ManifoldParams(plenum_volume_m3=2e-3, bore_area_m2=1.5e-3,
                        engine_draw_kgps=0.05)
    r = simulate_coupled_return([_spring()], I, mp,
                                ReturnResistance(friction_Nm=0.05), theta_open_deg=90)
    assert r.returns
    assert 0 < r.return_time_s < 1.0
    # engine draw pulls a vacuum: manifold min below ambient
    assert r.min_manifold_kpa < 101.325


def test_manifold_torque_coupling_slows_return():
    I = ThrottleInertia(5e-4, is_estimate=False)
    mp = ManifoldParams(engine_draw_kgps=0.05)
    res = ReturnResistance(friction_Nm=0.05)
    base = simulate_coupled_return([_spring()], I, mp, res, theta_open_deg=90,
                                   manifold_torque_coeff=0.0)
    held = simulate_coupled_return([_spring()], I, mp, res, theta_open_deg=90,
                                   manifold_torque_coeff=5e-5)
    assert base.returns
    # holding torque should not speed the return up
    if held.returns:
        assert held.return_time_s >= base.return_time_s


def test_no_engine_draw_means_no_vacuum():
    I = ThrottleInertia(5e-4, is_estimate=False)
    mp = ManifoldParams(engine_draw_kgps=0.0)   # engine off, plenum fills to ambient
    r = simulate_coupled_return([_spring()], I, mp,
                                ReturnResistance(friction_Nm=0.05))
    assert r.min_manifold_kpa == pytest.approx(101.325, abs=0.5)


# --------------------------------------------------------------------------- #
#  Flutter screen — physics + honesty
# --------------------------------------------------------------------------- #
def test_natural_frequency_matches_formula():
    I = ThrottleInertia(5e-4, is_estimate=False)
    fp = FlutterParams(k_theta_Nm_per_rad=2.0)
    r = screen_plate_flutter(I, fp)
    expected = math.sqrt(2.0 / 5e-4) / (2 * math.pi)
    assert r.natural_freq_hz == pytest.approx(expected, rel=1e-6)


def test_flutter_without_aero_coeff_is_flagged_not_claimed_stable():
    I = ThrottleInertia(5e-4, is_estimate=False)
    fp = FlutterParams(k_theta_Nm_per_rad=2.0, c_struct_Nms=1e-3, c_aero_Nms=0.0)
    r = screen_plate_flutter(I, fp, intake_speed_ms=50)
    assert r.aero_modelled is False
    # it must WARN that the aeroelastic part was not modelled — never a clean OK
    assert any(f.check == "throttle-flutter" and f.severity == Severity.WARN
               for f in r.findings)


def test_negative_aero_damping_goes_unstable_at_speed():
    I = ThrottleInertia(5e-4, is_estimate=False)
    fp = FlutterParams(k_theta_Nm_per_rad=2.0, c_struct_Nms=1e-3,
                       c_aero_Nms=-5e-3, c_aero_ref_speed_ms=30)
    lo = screen_plate_flutter(I, fp, intake_speed_ms=5)
    hi = screen_plate_flutter(I, fp, intake_speed_ms=60)
    assert lo.stable and not hi.stable
    assert hi.damping_ratio < 0
    assert hi.onset_speed_ms is not None and hi.onset_speed_ms > 0


def test_flutter_result_always_marked_as_screen():
    I = ThrottleInertia(5e-4, is_estimate=False)
    for c in (0.0, 2e-3, -2e-3):
        fp = FlutterParams(c_aero_Nms=c, c_aero_ref_speed_ms=30)
        r = screen_plate_flutter(I, fp, intake_speed_ms=40)
        assert r.is_screen is True     # never claims to be validation


def test_positive_aero_damping_is_stable_screen():
    I = ThrottleInertia(5e-4, is_estimate=False)
    fp = FlutterParams(k_theta_Nm_per_rad=2.0, c_struct_Nms=1e-3,
                       c_aero_Nms=2e-3, c_aero_ref_speed_ms=30)
    r = screen_plate_flutter(I, fp, intake_speed_ms=40)
    assert r.stable
    assert r.aero_modelled
    assert r.is_screen


# --------------------------------------------------------------------------- #
#  Package surface
# --------------------------------------------------------------------------- #
def test_symbols_exposed_from_package():
    import suspension
    for name in ("simulate_coupled_return", "screen_plate_flutter",
                 "ManifoldParams", "FlutterParams", "compressible_mass_flow",
                 "throttle_flow_area"):
        assert hasattr(suspension, name), name
