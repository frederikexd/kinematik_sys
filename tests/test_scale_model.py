# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Tests for scaled-model similitude, tolerance budget and mount alignment.

These pin the behaviour that makes the feature an honest planning tool rather than
false reassurance:

  1. the scale geometry is exact and invertible (40% <-> 1:2.5, full size recovered
     by division, never stored independently so it can't drift),
  2. Reynolds matching computes the right speed-up (a 40% model needs 2.5x speed)
     and tells the truth when the tunnel can't reach it — including the low-Re
     laminar-bubble warning that says the coefficient may not transfer at all,
  3. the tolerance budget turns a measured millimetre into a coefficient band,
     combines independent sources in quadrature, and refuses to call an UNMEASURED
     build zero-uncertainty,
  4. the Dzus-weld lesson (mount incidence error) folds into the same band,
  5. the honesty contract holds — the provenance string says SCALED and "not a
     full-size measurement", so a scaled coefficient can never be read as the car.

Run:  python -m pytest tests/test_scale_model.py
"""
import math

import pytest

from suspension.aero import (
    ScaleSpec, SimilitudePlan, ToleranceBudget, MountAlignment, ScaledRunPlan,
    reynolds, air_kinematic_viscosity, LOW_RE_BUBBLE_THRESHOLD,
)


# --- the meeting's exact decision, reused across tests --------------------- #
def meeting_spec():
    # "Scale will be 1:2.5 (40% of the original) with a chord length of 500,
    #  height of 260 and width of 250 mm"
    return ScaleSpec(ratio=0.4, scaled_chord_mm=500.0,
                     scaled_height_mm=260.0, scaled_width_mm=250.0)


# --- 1. scale geometry is exact and invertible ----------------------------- #
def test_scale_geometry_recovers_full_size_exactly():
    spec = meeting_spec()
    assert spec.inverse_ratio == pytest.approx(2.5)
    # full size = scaled / ratio
    assert spec.full_chord_mm == pytest.approx(1250.0)
    assert spec.full_height_mm == pytest.approx(650.0)
    assert spec.full_width_mm == pytest.approx(625.0)
    # area scales as ratio^2
    assert spec.area_ratio() == pytest.approx(0.16)


def test_scale_spec_rejects_nonsense():
    with pytest.raises(ValueError):
        ScaleSpec(ratio=0.0, scaled_chord_mm=500.0)
    with pytest.raises(ValueError):
        ScaleSpec(ratio=1.5, scaled_chord_mm=500.0)
    with pytest.raises(ValueError):
        ScaleSpec(ratio=0.4, scaled_chord_mm=-1.0)


def test_optional_dims_stay_none():
    spec = ScaleSpec(ratio=0.5, scaled_chord_mm=400.0)
    assert spec.full_height_mm is None
    assert spec.scaled_frontal_area_m2() is None


# --- 2. Reynolds similitude ------------------------------------------------ #
def test_reynolds_is_textbook():
    nu = air_kinematic_viscosity(15.0)
    re = reynolds(20.0, 1.25, nu)
    assert re == pytest.approx(20.0 * 1.25 / nu)


def test_matched_speed_is_full_speed_over_ratio():
    # A 40% model must run at 1/0.4 = 2.5x the full-size speed to match Reynolds.
    spec = meeting_spec()
    plan = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                         tunnel_max_speed_ms=200.0)
    assert plan.matched_speed_ms == pytest.approx(20.0 / 0.4, rel=1e-6)  # 50 m/s
    assert plan.reachable
    # at the matched speed the model Reynolds equals the full-size Reynolds
    assert plan.reynolds_match_ratio == pytest.approx(1.0, rel=1e-6)


def test_unreachable_tunnel_is_flagged_not_faked():
    spec = meeting_spec()
    # full car at 20 m/s needs 50 m/s on the model; a 45 m/s tunnel can't do it
    plan = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                         tunnel_max_speed_ms=45.0)
    assert not plan.reachable
    assert plan.achievable_speed_ms == pytest.approx(45.0)
    assert plan.reynolds_match_ratio < 1.0
    assert "NOT matched" in plan.verdict
    assert plan.warnings  # there is at least one explicit warning


def test_low_reynolds_bubble_warning_fires_for_tiny_slow_model():
    # a very small model at a low speed drops under the bubble threshold
    spec = ScaleSpec(ratio=0.2, scaled_chord_mm=120.0)
    plan = SimilitudePlan.match_reynolds(spec, full_speed_ms=8.0,
                                         tunnel_max_speed_ms=10.0)
    assert plan.achieved_reynolds < LOW_RE_BUBBLE_THRESHOLD
    assert any("laminar-separation" in w for w in plan.warnings)


def test_temperature_shifts_viscosity_the_right_way():
    # warmer air is more viscous (kinematically): nu rises with temperature
    assert air_kinematic_viscosity(30.0) > air_kinematic_viscosity(15.0)


# --- 3. tolerance budget --------------------------------------------------- #
def test_unmeasured_build_is_unknown_not_zero():
    budget = ToleranceBudget(meeting_spec())
    rep = budget.report()
    assert rep.cl_uncertainty_frac == 0.0
    assert "UNKNOWN" in rep.summary  # the honest word, not a reassuring zero


def test_chord_deviation_maps_to_fraction_of_chord():
    spec = meeting_spec()  # 500 mm scaled chord
    budget = ToleranceBudget(spec)
    budget.add_chord_deviation_mm(5.0)   # 1% of chord
    rep = budget.report()
    # chord error is ~1:1 onto C_l
    assert rep.cl_uncertainty_frac == pytest.approx(0.01, rel=1e-6)
    # ~0.5:1 onto C_d
    assert rep.cd_uncertainty_frac == pytest.approx(0.005, rel=1e-6)


def test_sources_combine_in_quadrature():
    spec = meeting_spec()
    budget = ToleranceBudget(spec)
    budget.add_chord_deviation_mm(5.0)    # cl 0.01
    budget.add_span_deviation_mm(10.0, scaled_span_mm=1000.0)  # cl 0.01
    rep = budget.report()
    # RSS of two 1% sources = sqrt(2)% , not 2%
    assert rep.cl_uncertainty_frac == pytest.approx(math.hypot(0.01, 0.01), rel=1e-6)


def test_camber_is_amplified_relative_to_chord():
    spec = meeting_spec()
    chord_only = ToleranceBudget(spec)
    chord_only.add_chord_deviation_mm(2.0)
    camber_only = ToleranceBudget(spec)
    camber_only.add_camber_deviation_mm(2.0)
    # same mm, but camber bites C_l harder than a plain chord error
    assert (camber_only.report().cl_uncertainty_frac
            > chord_only.report().cl_uncertainty_frac)


def test_waviness_loads_drag_not_lift():
    spec = meeting_spec()
    budget = ToleranceBudget(spec)
    budget.add_surface_waviness_mm(1.0)
    rep = budget.report()
    assert rep.cd_uncertainty_frac > rep.cl_uncertainty_frac


# --- 4. mount alignment (the Dzus-weld lesson) ----------------------------- #
def test_mount_incidence_becomes_cl_uncertainty():
    mnt = MountAlignment(incidence_error_deg=1.0)
    assert mnt.incidence_uncertainty_frac() == pytest.approx(0.08, rel=1e-6)
    assert "Dzus" in mnt.status()


def test_nominal_mount_reports_nominal():
    mnt = MountAlignment()
    assert mnt.incidence_uncertainty_frac() == 0.0
    assert "nominal" in mnt.status()


# --- 5. the run plan ties it together and stays honest --------------------- #
def test_run_plan_combines_tolerance_and_mount_in_quadrature():
    spec = meeting_spec()
    sim = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                        tunnel_max_speed_ms=60.0)
    tol = ToleranceBudget(spec)
    tol.add_chord_deviation_mm(5.0)  # cl 0.01
    mnt = MountAlignment(incidence_error_deg=0.5)  # cl 0.04
    plan = ScaledRunPlan(sim, tol, mnt)
    expected = math.hypot(0.01, 0.04)
    assert plan.combined_cl_uncertainty_frac() == pytest.approx(expected, rel=1e-6)


def test_provenance_says_scaled_and_not_full_size():
    spec = meeting_spec()
    sim = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                        tunnel_max_speed_ms=60.0)
    plan = ScaledRunPlan(sim, ToleranceBudget(spec))
    prov = plan.provenance()
    assert "SCALED MODEL" in prov
    assert "Not a full-size measurement" in prov
    # and it carries the model scale straight into the tunnel provenance fields
    assert plan.model_scale() == pytest.approx(0.4)
    assert plan.tunnel_reynolds() == pytest.approx(sim.achieved_reynolds)


def test_run_plan_report_is_human_readable():
    spec = meeting_spec()
    sim = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                        tunnel_max_speed_ms=45.0)
    tol = ToleranceBudget(spec)
    tol.add_chord_deviation_mm(2.0).add_camber_deviation_mm(1.5)
    plan = ScaledRunPlan(sim, tol, MountAlignment(incidence_error_deg=1.0))
    text = plan.report()
    assert "Scaled-model run plan" in text
    assert "similitude" in text
    assert "provenance" in text
