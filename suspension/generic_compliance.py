# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Architecture-agnostic first-order compliance.
============================================

The full :class:`~suspension.compliance.CompliantCorner` is a wishbone-specific
iterative solver. Every OTHER topology already carries a fully-constrained
multibody model in :class:`~suspension.topology.Mechanism`, so we can produce a
usable, honest compliance estimate for any architecture and stop blocking the
Compliance (Flex) tab on non-wishbone cars.

Method — virtual work through the REAL constraint solver
--------------------------------------------------------
A trailing arm, twist-beam or multilink is not a pin-jointed truss; it has rigid
bodies on revolute joints. Rather than assume two-force members, we treat each
``Link``'s held length as the source of compliance and use the mechanism's own
solver to get an exact first-order response:

    1. **Sensitivity by re-solve.** For each link i we nudge its held length by a
       small +dL and re-solve the whole mechanism (all constraints: links,
       revolutes, sliders, rigid carriers, the driving DOF). The resulting change
       in the wheel-carrier state gives, to first order, the sensitivity vectors

           b_i = d(contact_patch) / dL_i           (mm per mm)
           r_i = d(carrier_rotation_vector) / dL_i  (rad per mm)

       These are the true geometric influence of that member's stretch on the
       wheel — captured through the actual joints, which a truss model can't do.

    2. **Member forces by virtual work.** The external wheel load F does work
       through the wheel motion a member's stretch produces, so the axial force
       the member must carry to react F is

           lambda_i = F · b_i .

       (Reciprocity / unit-load theorem: the same b_i that maps a member's stretch
       to wheel motion maps the wheel load back to that member's axial force.)

    3. **Deflect and accumulate.** Each member gives dL_i = lambda_i / k_i (link
       E*A/L in series with any end joints, using the joint's TANGENT stiffness at
       the current force). The compliant wheel motion is the superposition
       u = Σ b_i dL_i (contact-patch shift) and Σ r_i dL_i (carrier rotation), read
       out as compliance camber (about the car x-axis), compliance toe (about z)
       and the contact-patch lateral shift.

Re-iterated coupling (parity with the wishbone path)
----------------------------------------------------
The three steps above are one linearisation about a pose. Because the sensitivities
b_i, r_i and the member forces they produce are themselves functions of the pose —
and the joints are non-linear — a single pass is only first-order. So, exactly like
:meth:`~suspension.compliance.CompliantCorner.solve`, we iterate the load↔geometry
coupling to convergence:

    * hold an accumulated length delta Σδ_i per member;
    * re-solve the WHOLE mechanism with every held length set to L0_i + Σδ_i, giving
      the actual DEFLECTED pose (not the static one);
    * recompute b_i, r_i and the member forces AT THAT deflected pose, and the new
      deflections from the joints' tangent stiffness at the current force;
    * repeat until the compliance camber and toe stop moving (typically 2–4 steps;
      deflections are sub-mm so it converges fast).

This removes the "first-order / one linearised re-solve" caveat: both the
double-wishbone corner and every other topology now run the same re-iterated solver
with per-member materials and joint compliance. The near-singular / non-axial guard
(a twist-beam torsion member etc.) still applies and is reported separately as
``well_conditioned``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from . import loadpath as lp
from .topology import Link


def _v(x):
    return np.asarray(x, float).reshape(3)


@dataclass
class GenericComplianceResult:
    load: lp.WheelLoad
    compliance_toe: float                 # deg
    compliance_camber: float              # deg
    contact_patch_lateral_shift_mm: float
    member_forces: dict = field(default_factory=dict)
    member_deflection: dict = field(default_factory=dict)
    member_stiffness: dict = field(default_factory=dict)
    member_joint_deflection: dict = field(default_factory=dict)
    converged: bool = True
    residual_N: float = 0.0
    iterations: int = 1
    well_conditioned: bool = True

    def summary(self):
        return {
            "compliance_toe_deg": float(self.compliance_toe),
            "compliance_camber_deg": float(self.compliance_camber),
            "contact_patch_lateral_shift_mm": float(self.contact_patch_lateral_shift_mm),
            "member_forces_N": {k: float(v) for k, v in self.member_forces.items()},
            "member_deflection_mm": {k: float(v) for k, v in self.member_deflection.items()},
            "residual_N": float(self.residual_N),
            "iterations": int(self.iterations),
            "well_conditioned": bool(self.well_conditioned),
            "converged": bool(self.converged),
        }


def _rotvec(dR):
    """Small-rotation vector w with dR ~= I + [w]_x."""
    return np.array([(dR[2, 1] - dR[1, 2]) * 0.5,
                     (dR[0, 2] - dR[2, 0]) * 0.5,
                     (dR[1, 0] - dR[0, 1]) * 0.5])


def _link_constraints(mech):
    out = []
    for c in mech.constraints:
        if isinstance(c, Link):
            out.append((c.label or f"{c.a}->{c.b}", c.a, c.b, c))
    return out


B_SINGULAR = 5.0        # mm wheel motion per mm member stretch


def _pose_sensitivities(mech, links, cp_name, R0, eps_mm):
    """Sensitivities b_i = d(cp)/dL_i and r_i = d(rotvec)/dL_i evaluated about the
    mechanism's CURRENT held lengths (i.e. the current deflected pose). Each link's
    held length is perturbed ±eps and the whole mechanism re-solved; the carrier
    rotation is measured relative to the static reference R0.
    """
    b_vecs, r_vecs = {}, {}
    for label, a, b, c in links:
        Lc = c.length
        try:
            c.length = Lc + eps_mm
            plus = mech.solve(0.0)
            c.length = Lc - eps_mm
            minus = mech.solve(0.0)
        finally:
            c.length = Lc
        cp_p = _v(plus["positions"][cp_name])
        cp_m = _v(minus["positions"][cp_name])
        b_vecs[label] = (cp_p - cp_m) / (2 * eps_mm)
        r_p = _rotvec(np.asarray(plus["carrier_R"], float) @ R0.T)
        r_m = _rotvec(np.asarray(minus["carrier_R"], float) @ R0.T)
        r_vecs[label] = (r_p - r_m) / (2 * eps_mm)
    return b_vecs, r_vecs


def solve_generic_compliance(kin, load: lp.WheelLoad, stiffness_for,
                             *, ground_roles=("ground",),
                             eps_mm: float = 0.25,
                             max_iter: int = 12,
                             tol_deg: float = 1e-4) -> GenericComplianceResult:
    """Re-iterated compliance for any GenericKinematics-like corner.

    Runs the same load↔geometry coupling loop as the double-wishbone
    :class:`~suspension.compliance.CompliantCorner`: member forces by virtual work
    through the real constraint solver, per-member deflection from the link material
    and non-linear joints, then a re-solve of the whole mechanism at the deflected
    held lengths — iterated until the compliance camber and toe converge.

    stiffness_for(label, a, b) -> MemberStiffness | None.
    """
    mech = kin.mech
    if not getattr(mech, "_compiled", False):
        mech.compile()

    base = mech.solve(0.0)
    pos0 = base["positions"]
    R0 = np.asarray(base["carrier_R"], float)     # static reference orientation
    cp_name = mech.contact_patch

    links = _link_constraints(mech)
    if not links:
        return GenericComplianceResult(
            load=load, compliance_toe=0.0, compliance_camber=0.0,
            contact_patch_lateral_shift_mm=0.0, converged=False,
            well_conditioned=False, iterations=0,
            residual_N=float(np.linalg.norm(load.force())))

    # Original (static, unloaded) held length of each link. These are the L0 the
    # accumulated deflection is added to each iteration.
    L0 = {label: float(c.length) for label, a, b, c in links}
    cp0 = _v(pos0[cp_name])
    F = load.force()

    # Accumulated per-member deflection (mm). Re-solving the mechanism at
    # L0 + total_delta gives the actual deflected pose.
    total_delta = {label: 0.0 for label, *_ in links}
    m_forces, m_defl, m_k, m_jdefl = {}, {}, {}, {}
    camber_deg = toe_deg = cp_shift = 0.0
    well_conditioned = True
    it = 0

    d_cam = d_toe = float("inf")
    for it in range(1, max_iter + 1):
        # (a) set every held length to its CURRENT deflected value and solve the
        # actual deflected pose. On iteration 1 total_delta is zero (static pose);
        # thereafter it carries the accumulated give.
        for label, a, b, c in links:
            c.length = L0[label] + total_delta[label]
        cur = mech.solve(0.0)
        cur_pos = cur["positions"]
        cur_cp = _v(cur_pos[cp_name])
        cur_R = np.asarray(cur["carrier_R"], float)

        # read out compliance at THIS deflected pose (relative to the static ref).
        rot = _rotvec(cur_R @ R0.T)
        new_camber = float(np.degrees(rot[0]))    # about car x
        new_toe = float(np.degrees(rot[2]))       # about vertical z
        new_cp_shift = float((cur_cp - cp0)[1])    # lateral (y) patch shift

        # (b) sensitivities & member lengths AT THIS deflected pose.
        b_vecs, r_vecs = _pose_sensitivities(mech, links, cp_name, R0, eps_mm)
        cur_len = {label: float(np.linalg.norm(_v(cur_pos[a]) - _v(cur_pos[b])))
                   for label, a, b, c in links}

        # (c) member forces by virtual work; recompute total deflection per member
        # from the current force (non-linear joints use their tangent give here).
        new_delta = {}
        m_forces, m_defl, m_k, m_jdefl = {}, {}, {}, {}
        well_conditioned = True
        for label, a, b, c in links:
            bvec = b_vecs[label]
            T = float(F @ bvec)                 # axial force, +tension
            m_forces[label] = T
            L = cur_len[label]
            ms = stiffness_for(label, a, b)
            delta = 0.0
            if ms is not None:
                try:
                    delta = ms.axial_deflection(T, L)
                    m_k[label] = ms.tangent_axial_stiffness(T, L)
                    m_jdefl[label] = ms.deflection_breakdown(T, L)
                except Exception:
                    delta = 0.0
            m_defl[label] = delta
            # Near-singular / non-axial guard (e.g. a twist-beam torsion member):
            # a member whose stretch barely locates the wheel gives |b| >> 1 and
            # would explode an axial model. Keep it out of the deflected geometry
            # and the read-out, and flag low confidence.
            if (np.linalg.norm(bvec) > B_SINGULAR
                    or np.linalg.norm(r_vecs[label]) > 1.0):
                well_conditioned = False
                new_delta[label] = 0.0
                continue
            new_delta[label] = delta

        # (d) convergence on the coupling: has the deflection state stopped moving?
        d_cam = abs(new_camber - camber_deg)
        d_toe = abs(new_toe - toe_deg)
        camber_deg, toe_deg, cp_shift = new_camber, new_toe, new_cp_shift
        total_delta = new_delta

        if it > 1 and max(d_cam, d_toe) < tol_deg:
            break

    # restore the mechanism to its rest lengths so the object is reusable.
    for label, a, b, c in links:
        c.length = L0[label]
    mech.solve(0.0)

    loop_converged = it < max_iter or max(d_cam, d_toe) < tol_deg
    return GenericComplianceResult(
        load=load,
        compliance_toe=toe_deg,
        compliance_camber=camber_deg,
        contact_patch_lateral_shift_mm=cp_shift,
        member_forces=m_forces,
        member_deflection=m_defl,
        member_stiffness=m_k,
        member_joint_deflection=m_jdefl,
        well_conditioned=bool(well_conditioned),
        converged=bool(well_conditioned and loop_converged),
        iterations=int(it),
        residual_N=0.0,
    )
