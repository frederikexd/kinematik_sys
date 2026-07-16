"""Tests for the topology-agnostic, re-iterated compliance estimator."""
import numpy as np
import pytest

import suspension.topologies as T
from suspension import (GenericKinematics, MemberStiffness,
                        solve_generic_compliance)
import suspension.loadpath as lp


def _stiff(label, a, b):
    return MemberStiffness(material="Steel 4130", od_mm=19.05, wall_mm=0.9)


ALL_TOPOS = ["macpherson_strut", "multilink", "trailing_arm",
             "semi_trailing_arm", "solid_axle", "twist_beam",
             "truck_steer_linkage"]


@pytest.mark.parametrize("topo", ALL_TOPOS)
def test_runs_for_every_topology(topo):
    kin = GenericKinematics(T.example(topo))
    load = lp.WheelLoad(Fx=200.0, Fy=-2500.0, Fz=1500.0)
    r = solve_generic_compliance(kin, load, _stiff)
    # Every architecture returns finite metrics and a member set.
    assert np.isfinite(r.compliance_toe)
    assert np.isfinite(r.compliance_camber)
    assert np.isfinite(r.contact_patch_lateral_shift_mm)
    assert len(r.member_forces) >= 1
    for v in r.member_forces.values():
        assert np.isfinite(v)


def test_zero_load_gives_zero_compliance():
    kin = GenericKinematics(T.example("macpherson_strut"))
    r = solve_generic_compliance(kin, lp.WheelLoad(), _stiff)
    assert abs(r.compliance_toe) < 1e-6
    assert abs(r.compliance_camber) < 1e-6
    assert abs(r.contact_patch_lateral_shift_mm) < 1e-6


def test_softer_tube_gives_more_compliance():
    kin = GenericKinematics(T.example("multilink"))
    load = lp.WheelLoad(Fy=-3000.0, Fz=1800.0)
    stiff_hi = lambda l, a, b: MemberStiffness(material="Steel 4130",
                                               od_mm=25.4, wall_mm=2.0)
    stiff_lo = lambda l, a, b: MemberStiffness(material="Aluminium 6061",
                                               od_mm=19.05, wall_mm=0.9)
    hi = solve_generic_compliance(kin, load, stiff_hi)
    lo = solve_generic_compliance(kin, load, stiff_lo)
    # A softer/thinner tube must not be stiffer: larger patch shift magnitude.
    assert abs(lo.contact_patch_lateral_shift_mm) >= abs(hi.contact_patch_lateral_shift_mm)


def test_twist_beam_flagged_low_confidence():
    # The twist-beam's torsion member is non-axial; the estimator should flag it
    # rather than emit an exploded number.
    kin = GenericKinematics(T.example("twist_beam"))
    load = lp.WheelLoad(Fy=-2500.0, Fz=1500.0)
    r = solve_generic_compliance(kin, load, _stiff)
    assert r.converged is False
    # And the reported rotation stays sane (not the tens-of-degrees blow-up).
    assert abs(r.compliance_camber) < 5.0


def test_reiterates_load_geometry_coupling():
    """The solver runs the coupling loop (more than one linearised pass) and
    reports the iteration count, matching the wishbone path's contract."""
    kin = GenericKinematics(T.example("multilink"))
    load = lp.WheelLoad(Fx=200.0, Fy=-2800.0, Fz=1600.0)
    r = solve_generic_compliance(kin, load, _stiff)
    assert r.iterations >= 2          # a genuine re-solve, not a single pass
    assert r.well_conditioned is True
    assert r.converged is True        # well-conditioned AND loop-converged
    # summary carries the new diagnostics
    s = r.summary()
    assert s["iterations"] == r.iterations
    assert s["well_conditioned"] == r.well_conditioned


def test_converged_requires_both_conditioning_and_loop():
    """`converged` now means well-conditioned AND the loop settled; the twist-beam
    is well-defined numerically but non-axial, so it must not read converged."""
    kin = GenericKinematics(T.example("twist_beam"))
    r = solve_generic_compliance(kin, lp.WheelLoad(Fy=-2500.0, Fz=1500.0), _stiff)
    assert r.well_conditioned is False
    assert r.converged is False


def test_iteration_cap_is_respected():
    """Even a marginally-stable corner returns within max_iter and stays finite."""
    kin = GenericKinematics(T.example("solid_axle"))
    load = lp.WheelLoad(Fx=200.0, Fy=-2500.0, Fz=1500.0)
    r = solve_generic_compliance(kin, load, _stiff, max_iter=12)
    assert 1 <= r.iterations <= 12
    assert np.isfinite(r.compliance_camber) and abs(r.compliance_camber) < 5.0
    assert np.isfinite(r.compliance_toe) and abs(r.compliance_toe) < 5.0


def test_mechanism_restored_after_solve():
    """The solver must leave every link's held length at its original value so the
    same GenericKinematics can be re-used (e.g. the UI re-runs on every slider)."""
    kin = GenericKinematics(T.example("macpherson_strut"))
    kin.mech.compile()
    import suspension.generic_compliance as gc
    before = {c.label or f"{c.a}->{c.b}": float(c.length)
              for _, _, _, c in gc._link_constraints(kin.mech)}
    solve_generic_compliance(kin, lp.WheelLoad(Fy=-3000.0, Fz=1800.0), _stiff)
    after = {c.label or f"{c.a}->{c.b}": float(c.length)
             for _, _, _, c in gc._link_constraints(kin.mech)}
    for k in before:
        assert abs(before[k] - after[k]) < 1e-9
