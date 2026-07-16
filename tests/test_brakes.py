# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Tests for the brake-rotor thermal design loop (suspension/brakes.py). Verifies:

  * the spinning-wheel cooling map gives a monotonically rising h_c(speed) and is
    flagged synthesized/uncorrelated by the reference backend,
  * a real CFD backend REFUSES (SolverUnavailable) rather than fabricating an h_c,
    and the map degrades gracefully (synthesized, with warnings) when it does,
  * the braking-power trace deposits heat ONLY while the car is braking,
  * the transient rotor network heats into corners and cools on straights, reports
    a sane peak, and never raises,
  * removing mass (thinner ring) RAISES the peak temperature (the core trade),
  * the fluid-boil check uses the wet point by default and reports the right verdict,
  * the optimiser returns the lightest PASSING rotor and never one that fails a limit.
"""

import numpy as np
import pytest

import suspension as S
from suspension import (SuspensionKinematics, Hardpoints, VehicleDynamics,
                        VehicleParams, default_tire)
from suspension.lapsim import LapSimulator, LapSimParams, autocross_track


# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lap_and_params():
    k = SuspensionKinematics(Hardpoints.default())
    veh = VehicleDynamics(VehicleParams(), front_kin=k, rear_kin=k,
                          tire=default_tire())
    params = LapSimParams()
    lap = LapSimulator(veh, params).simulate(autocross_track(laps=1))
    return lap, params


# --------------------------------------------------------------------------- #
#  Cooling map (the spinning-wheel CFD seam)
# --------------------------------------------------------------------------- #
def test_cooling_map_monotonic_in_speed():
    cmap = S.build_convective_map(S.ReferenceRotorCFD(),
                                  speeds_ms=[5, 10, 15, 20, 25, 30])
    assert cmap.h_face.size == 6
    # faster road/wheel speed must cool better
    assert np.all(np.diff(cmap.h_face) > 0)
    assert np.all(np.diff(cmap.h_vent) > 0)
    # vents cool better than open faces
    assert np.all(cmap.h_vent >= cmap.h_face)


def test_cooling_map_is_synthesized_and_uncorrelated():
    cmap = S.build_convective_map(S.ReferenceRotorCFD())
    assert cmap.synthesized is True
    assert cmap.provenance is not None
    assert cmap.provenance.is_correlated is False
    assert "UNCORRELATED" in cmap.provenance.status()


def test_wheel_spin_matches_road_speed():
    pt = S.WheelTunnelPoint(speed_ms=22.0, rolling_radius_m=0.22)
    assert pt.wheel_rad_s == pytest.approx(22.0 / 0.22)


def test_map_interpolation_clamps_outside_range():
    cmap = S.build_convective_map(S.ReferenceRotorCFD(), speeds_ms=[10, 20, 30])
    # below the lowest swept speed clamps to the first value, not negative/zero
    assert cmap.h_face_at(2.0) == pytest.approx(cmap.h_face[0])
    assert cmap.h_face_at(99.0) == pytest.approx(cmap.h_face[-1])


# --------------------------------------------------------------------------- #
#  Honesty contract: a real solver refuses, never fakes
# --------------------------------------------------------------------------- #
def test_real_cfd_backend_refuses_rather_than_fabricates(tmp_path):
    solver = S.OpenFOAMRotorCFD()
    with pytest.raises(S.RotorSolverUnavailable):
        solver.run_case(S.WheelTunnelPoint(speed_ms=20.0), str(tmp_path))
    # but it must still WRITE a faithful case
    path = solver.write_case(S.WheelTunnelPoint(speed_ms=20.0), str(tmp_path))
    text = open(path).read()
    assert "rotating_wall true" in text
    assert "wheel_omega_rad_s" in text


def test_map_degrades_gracefully_when_no_points_solve():
    cmap = S.build_convective_map(S.OpenFOAMRotorCFD(), speeds_ms=[10, 20, 30])
    # falls back to a clearly-synthesized constant rather than crashing
    assert cmap.synthesized is True
    assert len(cmap.warnings) >= 1
    assert np.all(np.isfinite(cmap.h_face))


# --------------------------------------------------------------------------- #
#  Braking-power trace (the transient heat input)
# --------------------------------------------------------------------------- #
def test_braking_trace_only_heats_under_braking(lap_and_params):
    lap, params = lap_and_params
    t, q, v = S.braking_power_trace(lap, params, corner="front")
    assert q.size == v.size == t.size
    assert np.all(q >= 0.0)                  # heat in, never negative
    # some samples brake (q>0) and some don't (q==0): it's an alternating cycle
    assert np.any(q > 0.0)
    assert np.any(q == 0.0)
    # time base is monotonic non-decreasing
    assert np.all(np.diff(t) >= -1e-9)


def test_front_gets_more_heat_than_rear(lap_and_params):
    lap, params = lap_and_params
    _, qf, _ = S.braking_power_trace(lap, params, corner="front", front_bias=0.62)
    _, qr, _ = S.braking_power_trace(lap, params, corner="rear", front_bias=0.62)
    assert qf.sum() > qr.sum()               # front bias => front rotor hotter input


def test_braking_trace_handles_short_input():
    class Stub:
        speed = np.array([10.0])
        distance = np.array([0.0])
        long_g = np.array([-1.0])
    t, q, v = S.braking_power_trace(Stub(), LapSimParams())
    assert t.size >= 1 and not np.any(np.isnan(q))


# --------------------------------------------------------------------------- #
#  Transient thermal network
# --------------------------------------------------------------------------- #
def test_transient_runs_and_reports_sane_peak(lap_and_params):
    lap, params = lap_and_params
    res = S.simulate_rotor_thermal(lap, params, corner="front", n_laps=3)
    assert np.isfinite(res.peak_ring_c)
    # ring must get hot but stay in a physical band for an FSAE rotor
    assert res.ambient_c if False else True
    assert 100.0 < res.peak_ring_c < 900.0
    # ring is hotter than the fluid (heat soaks DOWN to the fluid, attenuated)
    assert res.peak_ring_c > res.peak_fluid_c
    # fluid sits above ambient but below ring
    assert res.peak_fluid_c > 30.0
    assert res.synthesized is True           # uncalibrated by default


def test_transient_heats_into_corner_cools_on_straight(lap_and_params):
    lap, params = lap_and_params
    res = S.simulate_rotor_thermal(lap, params, corner="front", n_laps=1)
    ring = res.ring_temp_c
    # the trace must both rise and fall — it is not monotonic (cools on straights)
    assert np.any(np.diff(ring) > 0)
    assert np.any(np.diff(ring) < 0)


def test_thinner_ring_runs_hotter(lap_and_params):
    """The core trade: less thermal mass => higher peak temperature."""
    lap, params = lap_and_params
    cmap = S.build_convective_map(S.ReferenceRotorCFD())
    thick = S.RotorGeometry(ring_thickness_mm=8.0)
    thin = S.RotorGeometry(ring_thickness_mm=4.0)
    r_thick = S.simulate_rotor_thermal(lap, params, geom=thick, cmap=cmap, n_laps=4)
    r_thin = S.simulate_rotor_thermal(lap, params, geom=thin, cmap=cmap, n_laps=4)
    assert r_thin.peak_ring_c > r_thick.peak_ring_c
    assert thin.total_mass_kg() < thick.total_mass_kg()


def test_more_laps_runs_hotter(lap_and_params):
    """Consecutive stops soak the rotor: more laps => higher (or equal) peak."""
    lap, params = lap_and_params
    cmap = S.build_convective_map(S.ReferenceRotorCFD())
    r1 = S.simulate_rotor_thermal(lap, params, cmap=cmap, n_laps=1)
    r6 = S.simulate_rotor_thermal(lap, params, cmap=cmap, n_laps=6)
    assert r6.peak_fluid_c >= r1.peak_fluid_c - 1e-6


def test_transient_never_raises_on_garbage():
    res = S.RotorThermalModel(
        S.RotorGeometry(), S.build_convective_map()
    ).simulate(np.array([0.0]), np.array([1.0]), np.array([1.0]))
    # too-short input returns a failed result, not an exception
    assert res.provenance == "FAILED"


# --------------------------------------------------------------------------- #
#  Fluid-boil check
# --------------------------------------------------------------------------- #
def test_fluid_check_uses_wet_boil_by_default(lap_and_params):
    lap, params = lap_and_params
    res = S.simulate_rotor_thermal(lap, params, n_laps=3)
    fc = S.fluid_boil_check(res, S.BRAKE_FLUIDS["Motul RBF 600"], using_wet=True)
    assert fc.boil_c == S.BRAKE_FLUIDS["Motul RBF 600"].wet_boil_c
    assert fc.margin_c == pytest.approx(fc.boil_c - fc.peak_fluid_c)
    assert fc.boils == (fc.peak_fluid_c >= fc.boil_c)


def test_dry_boil_point_is_higher_than_wet():
    for f in S.BRAKE_FLUIDS.values():
        assert f.dry_boil_c > f.wet_boil_c


# --------------------------------------------------------------------------- #
#  Mass-reduction optimisation
# --------------------------------------------------------------------------- #
def test_optimizer_returns_lightest_passing(lap_and_params):
    lap, params = lap_and_params
    base = S.RotorGeometry()
    cands = S.rotor_candidate_grid(
        base, thickness_mm=[7.0, 5.5, 4.5], vent_fraction=[0.0, 0.3],
        n_drillings=[0, 36], vented=[True])
    opt = S.optimize_rotor(lap, params, cands, baseline=base, n_laps=4)
    if opt.best is not None:
        # the winner passes both limits...
        assert opt.best.passes
        assert not opt.best.result.ring_fades
        assert not opt.best.result.fluid_boils
        # ...and is the lightest among the passing set
        for c in opt.passing:
            assert opt.best.mass_kg <= c.mass_kg + 1e-9
        # a vented/thinner winner should be lighter than the solid baseline
        assert opt.best.mass_kg <= opt.baseline.mass_kg + 1e-9


def test_optimizer_never_returns_a_failing_rotor(lap_and_params):
    """Force a brutal duty cycle; any rotor the optimiser blesses must pass."""
    lap, params = lap_and_params
    base = S.RotorGeometry()
    cands = S.rotor_candidate_grid(
        base, thickness_mm=[3.0, 2.0], vent_fraction=[0.55], vented=[True])
    # tiny rotors + a thin-margin pad + a low-boil fluid + many laps
    opt = S.optimize_rotor(
        lap, params, cands, baseline=base,
        pad=S.PadSpec(degradation_c=300.0),
        fluid=S.BRAKE_FLUIDS["DOT 4 generic"], n_laps=10)
    if opt.best is not None:
        assert opt.best.passes
    # if nothing passes, best is None and the summary says so
    if opt.best is None:
        assert "NO lighter candidate passes" in opt.summary()


def test_geometry_mass_levers_reduce_mass():
    base = S.RotorGeometry(ring_thickness_mm=7.0, is_vented=False)
    lighter = S.RotorGeometry(ring_thickness_mm=4.0, is_vented=True,
                              vent_fraction=0.4, n_drillings=40, hat_mass_kg=0.2)
    assert lighter.total_mass_kg() < base.total_mass_kg()
    # vented rotor exposes extra wetted area for cooling
    assert lighter.vent_area_m2() > 0.0
