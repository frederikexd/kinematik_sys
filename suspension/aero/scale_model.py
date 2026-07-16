# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Scale-model planning, similitude, and a manufacturing tolerance budget — the
bridge between a coefficient measured on a SCALED, hand-made part and the full-car
number the lap sim actually wants.

WHY THIS MODULE EXISTS (read this before trusting a scaled-model number)
------------------------------------------------------------------------
A team almost never tests the full-size part first. Material is finite, the oven /
tunnel / print bed is finite, and a scaled article lets you make two and break one.
So the real first decision in an aero validation programme is the one that reads,
in a meeting minute, like a throwaway line:

    "Decided to do a scaled version instead of original dimensions since material
     may not be enough ... Scale will be 1:2.5 (40% of original) with a chord of
     500, height 260, width 250 mm ... We have to carefully measure and check the
     tolerance."

That single decision quietly mortgages the whole validation, in three ways, and
NONE of them is captured anywhere else in `suspension.aero`:

  1. SIMILITUDE.  A 40%-scale wing at the same wind speed runs at 40% of the
     Reynolds number. Below roughly Re ~ 2e5 on the chord, a low-Re laminar
     separation bubble appears that the full-size part never sees — the scaled C_l
     is then NOT the full-size C_l, and no amount of careful measuring fixes it.
     The honest move is to compute the SPEED that restores Reynolds match, check
     whether the tunnel can actually reach it, and if it cannot, say so loudly and
     quantify the residual mismatch instead of pretending the coefficient transfers.

  2. TOLERANCE -> UNCERTAINTY.  "Check the tolerance" is not a quality-control
     box-tick; it is an aero statement. A chord built to +-2 mm at 500 mm scaled
     chord is +-0.4% in chord, but a leading-edge or camber error of the same
     absolute size is a much larger fraction of the feature that sets C_l, and on a
     moldless / boss-extrude part (no female tool to copy a surface) the achievable
     tolerance is coarser than a CNC mold would give. This module turns a measured
     build deviation into a coefficient uncertainty band, so the correlation report
     downstream can say "CFD landed inside the part's own build tolerance" — or that
     it didn't, which is just as useful.

  3. MOUNT ALIGNMENT.  The lesson the manufacturing leads keep relearning — "the
     mounting tabs can move during welding, so do NOT drill or finalise the Dzus
     holes before the welding is complete" — is, in aero terms, an ANGLE-OF-INCIDENCE
     uncertainty. A nosecone or wing positioned 1 deg off because a tab crept while
     hot is a 1 deg shift in every coefficient. `MountAlignment` records the as-built
     incidence/position error and folds it into the same uncertainty band, so a
     suspiciously-off tunnel point can be checked against "could this just be the
     weld-induced misalignment?" before anyone blames the CFD.

WHAT THIS MODULE OWNS (and what it deliberately does not)
---------------------------------------------------------
  * `ScaleSpec`          — the geometric scale decision (ratio + scaled chord/
                           height/width), with the full-size dimensions recovered
                           exactly, in the established KinematiK convention.
  * `air_kinematic_viscosity` / `reynolds` — the textbook similitude arithmetic, in
                           one tested place, so a 0.5-vs-0.4 scale slip can't quietly
                           decorrelate a run.
  * `SimilitudePlan`     — given a ScaleSpec, a target full-size condition and a
                           tunnel's max speed, the matched-Reynolds speed, whether it
                           is reachable, and an HONEST verdict + residual when it is
                           not (the low-Re-bubble warning is first-class).
  * `ToleranceBudget`    — measured build deviations (chord, camber, span, surface
                           waviness) -> a combined fractional coefficient uncertainty,
                           using transparent published-order sensitivities, every one
                           labelled an ESTIMATE, never a measurement.
  * `MountAlignment`     — as-built incidence / position error (the Dzus-weld lesson)
                           -> the incidence contribution to that same band.
  * `ScaledRunPlan`      — ties the three together for one planned scaled run and
                           emits a `provenance` string that a `TunnelProvenance` /
                           `PhysicalAeroMap` can carry, so a coefficient measured on
                           the scaled part is never read as if it were the full car.

DELIBERATE NON-GOALS, same discipline as the rest of `suspension.aero`: this module
does not solve a flow, does not mesh, and does not invent a coefficient. Every
sensitivity it uses to turn a millimetre into a coefficient percentage is a stated,
order-of-magnitude ENGINEERING ESTIMATE carried in the result text — it sizes an
uncertainty band, it never produces a force. A hole it reports (tunnel too slow,
tolerance unmeasured) is a real hole.

Quick start (runnable today, no solver, no tunnel):
    from suspension.aero import ScaleSpec, SimilitudePlan, ToleranceBudget

    # the meeting's decision, verbatim
    spec = ScaleSpec(ratio=0.4, scaled_chord_mm=500, scaled_height_mm=260,
                     scaled_width_mm=250)
    print(spec.full_size_label())          # what the 1:2.5 part stands in for

    plan = SimilitudePlan.match_reynolds(spec, full_speed_ms=20.0,
                                         tunnel_max_speed_ms=45.0)
    print(plan.verdict)                    # reachable? residual Re mismatch?

    budget = ToleranceBudget(spec)
    budget.add_chord_deviation_mm(2.0)     # measured at the part
    budget.add_camber_deviation_mm(1.5)
    print(budget.report().summary)         # +- on C_l / C_d from the build alone
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
#  Air properties — one tested place for the similitude arithmetic
# --------------------------------------------------------------------------- #
#
# Standard sea-level air. Kinematic viscosity nu = mu / rho varies with
# temperature; a tunnel run in a warm shop is a few percent off the textbook
# value, which is why `reynolds` takes nu as an argument with a sane default rather
# than burying a constant. These are the SAME defaults the CFD seam uses
# (rho = 1.225) so a scaled-run Reynolds and a CFD-case Reynolds are computed on the
# same air and can be compared without a hidden unit slip.
DEFAULT_AIR_DENSITY = 1.225               # kg/m^3, ISA sea level, 15 C
DEFAULT_AIR_KINEMATIC_VISCOSITY = 1.48e-5  # m^2/s, ISA sea level, 15 C


def air_kinematic_viscosity(temp_c: float = 15.0) -> float:
    """
    Kinematic viscosity of air at a shop temperature, m^2/s. Sutherland for mu, ideal
    gas for rho, so a 30 C summer build floor (nu ~ 8% higher than 15 C) doesn't
    silently shift a matched-Reynolds speed. Honest within a couple percent over the
    0-40 C range a tunnel actually runs in; not meant for cryogenic tunnels.
    """
    t_k = temp_c + 273.15
    # Sutherland's law for dynamic viscosity (mu), Pa.s
    mu_ref, t_ref, s = 1.716e-5, 273.15, 110.4
    mu = mu_ref * (t_k / t_ref) ** 1.5 * (t_ref + s) / (t_k + s)
    # ideal-gas density at 1 atm
    rho = 101325.0 / (287.05 * t_k)
    return mu / rho


def reynolds(speed_ms: float, length_m: float,
             nu: float = DEFAULT_AIR_KINEMATIC_VISCOSITY) -> float:
    """Chord/length Reynolds number Re = V L / nu. The whole point of similitude."""
    if length_m <= 0 or nu <= 0:
        raise ValueError("length and viscosity must be positive")
    return speed_ms * length_m / nu


# Below this chord Reynolds, an FSAE-thickness aerofoil typically grows a laminar
# separation bubble the full-size part never has, so the scaled coefficient stops
# being the full-size coefficient. Not a sharp cliff — a widely-cited order marker.
LOW_RE_BUBBLE_THRESHOLD = 2.0e5


# --------------------------------------------------------------------------- #
#  The scale decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScaleSpec:
    """
    The geometric scaling decision, exactly as a team minutes it: a ratio (0.4 = 40%
    = 1:2.5) and the SCALED part's headline dimensions. The full-size dimensions are
    recovered by dividing by the ratio — no independent full-size numbers are stored,
    so the two can never drift apart.

    `chord` is the streamwise reference length that sets the part's Reynolds number;
    height and width are carried for blockage and packaging, not similitude. Lengths
    in mm to match the shop and the rest of the aero package; `*_m` helpers convert.
    """
    ratio: float                     # scaled / full-size, 0 < ratio <= 1
    scaled_chord_mm: float           # streamwise reference length OF THE MODEL
    scaled_height_mm: Optional[float] = None
    scaled_width_mm: Optional[float] = None
    note: str = ""

    def __post_init__(self):
        if not (0.0 < self.ratio <= 1.0):
            raise ValueError("ratio must be in (0, 1]; 0.4 means a 40%/1:2.5 model")
        if self.scaled_chord_mm <= 0:
            raise ValueError("scaled chord must be positive")

    # -- the recovered full-size part -------------------------------------- #
    @property
    def full_chord_mm(self) -> float:
        return self.scaled_chord_mm / self.ratio

    @property
    def full_height_mm(self) -> Optional[float]:
        return None if self.scaled_height_mm is None else self.scaled_height_mm / self.ratio

    @property
    def full_width_mm(self) -> Optional[float]:
        return None if self.scaled_width_mm is None else self.scaled_width_mm / self.ratio

    @property
    def inverse_ratio(self) -> float:
        """The "1:N" the team says out loud. 0.4 -> 2.5."""
        return 1.0 / self.ratio

    def scaled_chord_m(self) -> float:
        return self.scaled_chord_mm / 1000.0

    def full_chord_m(self) -> float:
        return self.full_chord_mm / 1000.0

    # -- areas scale as ratio^2 (the C_l/C_d non-dimensionalisation lever) -- #
    def area_ratio(self) -> float:
        return self.ratio * self.ratio

    def scaled_frontal_area_m2(self) -> Optional[float]:
        if self.scaled_height_mm is None or self.scaled_width_mm is None:
            return None
        return (self.scaled_height_mm / 1000.0) * (self.scaled_width_mm / 1000.0)

    def label(self) -> str:
        return (f"1:{self.inverse_ratio:g} ({self.ratio*100:g}%) — model chord "
                f"{self.scaled_chord_mm:g} mm")

    def full_size_label(self) -> str:
        parts = [f"chord {self.full_chord_mm:.0f}"]
        if self.full_height_mm is not None:
            parts.append(f"height {self.full_height_mm:.0f}")
        if self.full_width_mm is not None:
            parts.append(f"width {self.full_width_mm:.0f}")
        return (f"{self.label()} stands in for a full-size part: "
                + ", ".join(parts) + " mm")


# --------------------------------------------------------------------------- #
#  Similitude — does the scaled run see the same flow as the full car?
# --------------------------------------------------------------------------- #
@dataclass
class SimilitudePlan:
    """
    The Reynolds-similarity verdict for one scaled run. Built by `match_reynolds`,
    which answers the only question that decides whether a scaled coefficient is even
    allowed to transfer: at what wind speed does the SCALED chord run at the SAME
    Reynolds number as the FULL-SIZE chord at race speed — and can the tunnel reach
    it?

    Nothing here is fabricated. If the tunnel tops out below the matched speed, the
    plan does not quietly accept a mismatch; it reports the speed it CAN reach, the
    Reynolds number that produces, the residual mismatch as a ratio, and — when the
    scaled run falls under the low-Re bubble threshold — an explicit warning that the
    coefficient may not be the full-size coefficient at all.
    """
    spec: ScaleSpec
    full_speed_ms: float
    full_reynolds: float
    matched_speed_ms: float          # tunnel speed that restores Re on the model
    achievable_speed_ms: float       # min(matched, tunnel max)
    achieved_reynolds: float         # Re actually obtained at achievable speed
    reynolds_match_ratio: float      # achieved / full; 1.0 is perfect similitude
    reachable: bool
    nu: float
    verdict: str = ""
    warnings: tuple = ()

    @classmethod
    def match_reynolds(cls, spec: ScaleSpec, full_speed_ms: float,
                       tunnel_max_speed_ms: Optional[float] = None,
                       temp_c: float = 15.0) -> "SimilitudePlan":
        """
        Compute the matched-Reynolds tunnel speed for a scaled model.

        Re_full = V_full * L_full / nu  must equal  Re_model = V_model * L_model / nu.
        With L_model = ratio * L_full, the matched model speed is V_full / ratio:
        a 40% model must be run 1/0.4 = 2.5x faster to see the same Reynolds number.
        That speed-up is exactly why scaled aero so often CAN'T match Reynolds — it is
        the trap this method exists to surface.
        """
        nu = air_kinematic_viscosity(temp_c)
        l_full = spec.full_chord_m()
        l_model = spec.scaled_chord_m()
        re_full = reynolds(full_speed_ms, l_full, nu)

        # matched model speed: V_model = Re_full * nu / L_model = V_full / ratio
        matched_speed = re_full * nu / l_model
        if tunnel_max_speed_ms is None:
            achievable = matched_speed
            reachable = True
        else:
            achievable = min(matched_speed, tunnel_max_speed_ms)
            reachable = matched_speed <= tunnel_max_speed_ms + 1e-9

        re_achieved = reynolds(achievable, l_model, nu)
        match_ratio = re_achieved / re_full if re_full > 0 else 0.0

        warnings = []
        if not reachable:
            warnings.append(
                f"tunnel tops out at {tunnel_max_speed_ms:g} m/s but Reynolds match "
                f"needs {matched_speed:.1f} m/s; the scaled run is at "
                f"{match_ratio*100:.0f}% of full-size Reynolds")
        if re_achieved < LOW_RE_BUBBLE_THRESHOLD:
            warnings.append(
                f"achieved chord Re={re_achieved:.2e} is below the ~{LOW_RE_BUBBLE_THRESHOLD:.0e} "
                f"laminar-separation-bubble threshold; the scaled C_l/C_d may not be "
                f"the full-size value regardless of how well the part is built — "
                f"consider a turbulator/trip strip or a larger scale")

        if reachable and not warnings:
            verdict = (f"Reynolds MATCHED at {achievable:.1f} m/s "
                       f"(Re={re_achieved:.2e}); scaled coefficients transfer to "
                       f"full size within similitude.")
        elif reachable:
            verdict = (f"Reynolds matched at {achievable:.1f} m/s, but: "
                       + " ; ".join(warnings))
        else:
            verdict = (f"Reynolds NOT matched. Best reachable {achievable:.1f} m/s "
                       f"gives Re={re_achieved:.2e} = {match_ratio*100:.0f}% of the "
                       f"full-size Re={re_full:.2e}. " + " ; ".join(warnings))

        return cls(
            spec=spec, full_speed_ms=full_speed_ms, full_reynolds=re_full,
            matched_speed_ms=matched_speed, achievable_speed_ms=achievable,
            achieved_reynolds=re_achieved, reynolds_match_ratio=match_ratio,
            reachable=reachable, nu=nu, verdict=verdict, warnings=tuple(warnings),
        )

    def provenance_note(self) -> str:
        """One line a TunnelProvenance can carry so the scaled run is never read as full-size."""
        status = "Re-matched" if (self.reachable and not self.warnings) else "Re-MISMATCHED"
        return (f"{self.spec.label()}; {status} "
                f"(model Re={self.achieved_reynolds:.2e}, "
                f"{self.reynolds_match_ratio*100:.0f}% of full-size)")


# --------------------------------------------------------------------------- #
#  Tolerance budget — "carefully measure and check the tolerance" as an aero number
# --------------------------------------------------------------------------- #
#
# Each entry turns a measured BUILD DEVIATION (a millimetre off the CAD) into a
# fractional coefficient uncertainty via a transparent, stated sensitivity. The
# sensitivities are deliberately conservative order-of-magnitude engineering
# estimates from thin-aerofoil theory and published FSAE build studies, NOT measured
# constants for this specific wing — every report says so. The contributions combine
# in quadrature (independent error sources), which is the standard, honest way to
# stack tolerances rather than the alarmist linear sum or the dishonest "take the
# biggest".
@dataclass
class _ToleranceContribution:
    source: str
    deviation_mm: float
    cl_frac: float           # fractional uncertainty contributed to C_l
    cd_frac: float           # fractional uncertainty contributed to C_d
    basis: str               # the stated sensitivity used, for auditability


@dataclass
class ToleranceReport:
    contributions: tuple
    cl_uncertainty_frac: float    # combined +- fraction on C_l
    cd_uncertainty_frac: float    # combined +- fraction on C_d
    summary: str


class ToleranceBudget:
    """
    Accumulate the as-built deviations of a scaled part and report the coefficient
    uncertainty they imply. This is the quantitative meaning of a meeting line like
    "we have to carefully measure and check the tolerance": a part built to +-X mm
    carries a +-Y% aero uncertainty, and a correlation against CFD that lands inside
    +-Y% has confirmed nothing the build tolerance didn't already allow.

    Deviations are entered as the MEASURED (or specified) absolute error in mm at the
    SCALED part. They are non-dimensionalised against the scaled chord, because a
    1 mm error on a 500 mm scaled chord is a larger fraction — and therefore a larger
    aero error — than the same 1 mm on the 1250 mm full-size part. That is precisely
    why a coarse, moldless, boss-extrude build needs this check more than a CNC-molded
    full-size one does.
    """

    def __init__(self, spec: ScaleSpec):
        self.spec = spec
        self._contribs: list[_ToleranceContribution] = []

    # -- the standard deviation channels ----------------------------------- #
    def add_chord_deviation_mm(self, dev_mm: float) -> "ToleranceBudget":
        """
        Streamwise chord built long/short. To first order an aerofoil's lift and drag
        scale with chord (it sets the reference and the loaded area), so the
        fractional chord error maps ~1:1 to a C_l reference error and ~0.5:1 to C_d.
        """
        frac = abs(dev_mm) / self.spec.scaled_chord_mm
        self._contribs.append(_ToleranceContribution(
            "chord length", dev_mm, cl_frac=frac, cd_frac=0.5 * frac,
            basis="fractional chord error, ~1:1 on C_l reference, ~0.5:1 on C_d"))
        return self

    def add_camber_deviation_mm(self, dev_mm: float) -> "ToleranceBudget":
        """
        Camber / leading-edge profile off the CAD — the deviation that matters most on
        a moldless part with no female tool to copy the surface. Thin-aerofoil theory
        makes lift roughly linear in camber, and a high-load FSAE element carries a
        few % camber, so a camber error is amplified ~3x relative to its chord
        fraction onto C_l (and drives separation, hence a similar hit to C_d).
        """
        frac = abs(dev_mm) / self.spec.scaled_chord_mm
        self._contribs.append(_ToleranceContribution(
            "camber / LE profile", dev_mm, cl_frac=3.0 * frac, cd_frac=2.0 * frac,
            basis="thin-aerofoil camber sensitivity, ~3x chord-fraction onto C_l"))
        return self

    def add_span_deviation_mm(self, dev_mm: float, scaled_span_mm: float) -> "ToleranceBudget":
        """
        Span built off-nominal. Lift scales with planform area, so a span error is a
        near-1:1 fraction of span onto C_l; second-order on C_d via aspect ratio.
        """
        if scaled_span_mm <= 0:
            raise ValueError("scaled span must be positive")
        frac = abs(dev_mm) / scaled_span_mm
        self._contribs.append(_ToleranceContribution(
            "span", dev_mm, cl_frac=frac, cd_frac=0.3 * frac,
            basis="fractional span error, ~1:1 on C_l via planform area"))
        return self

    def add_surface_waviness_mm(self, dev_mm: float) -> "ToleranceBudget":
        """
        Surface waviness / print-layer steps / weave print-through — the texture a
        moldless or 3D-printed part has and a polished mold doesn't. It barely touches
        C_l but trips the boundary layer and adds pressure/parasitic drag, so it loads
        almost entirely onto C_d. Non-dimensionalised against chord like the rest.
        """
        frac = abs(dev_mm) / self.spec.scaled_chord_mm
        self._contribs.append(_ToleranceContribution(
            "surface waviness", dev_mm, cl_frac=0.2 * frac, cd_frac=2.5 * frac,
            basis="roughness/waviness trips BL; loads onto C_d (~2.5x), little on C_l"))
        return self

    def add_custom(self, source: str, cl_frac: float, cd_frac: float,
                   basis: str, dev_mm: float = 0.0) -> "ToleranceBudget":
        """Escape hatch for a team's own measured sensitivity. Honesty preserved: the basis is recorded."""
        self._contribs.append(_ToleranceContribution(source, dev_mm,
                                                      abs(cl_frac), abs(cd_frac), basis))
        return self

    # -- the deliverable --------------------------------------------------- #
    def report(self) -> ToleranceReport:
        if not self._contribs:
            return ToleranceReport(
                contributions=(), cl_uncertainty_frac=0.0, cd_uncertainty_frac=0.0,
                summary="no tolerance entered yet — measure the built part and add "
                        "its deviations; an unmeasured build has UNKNOWN, not zero, "
                        "uncertainty")
        # independent sources -> quadrature (RSS) combination
        cl = math.sqrt(sum(c.cl_frac ** 2 for c in self._contribs))
        cd = math.sqrt(sum(c.cd_frac ** 2 for c in self._contribs))
        lines = [f"build tolerance implies +-{cl*100:.1f}% on C_l, "
                 f"+-{cd*100:.1f}% on C_d (RSS of independent sources):"]
        for c in sorted(self._contribs, key=lambda x: -x.cl_frac):
            lines.append(f"  - {c.source}: {c.deviation_mm:+.2f} mm "
                         f"-> C_l +-{c.cl_frac*100:.1f}%, C_d +-{c.cd_frac*100:.1f}% "
                         f"[{c.basis}]")
        lines.append("  NOTE: sensitivities are order-of-magnitude ENGINEERING "
                     "ESTIMATES, not measured for this wing — they size a band, not a force.")
        return ToleranceReport(
            contributions=tuple(self._contribs),
            cl_uncertainty_frac=cl, cd_uncertainty_frac=cd,
            summary="\n".join(lines))


# --------------------------------------------------------------------------- #
#  Mount alignment — the Dzus-weld lesson, as an angle-of-incidence uncertainty
# --------------------------------------------------------------------------- #
@dataclass
class MountAlignment:
    """
    The as-built mounting error of the part on the car/rig — the aero translation of
    the manufacturing leads' hard-won rule that "mounting tabs can move during
    welding, so do NOT drill or finalise the Dzus holes before welding is complete."
    A tab that crept while hot leaves the nosecone or wing sitting at an incidence a
    degree or two off nominal, and EVERY coefficient shifts with incidence.

    Record what you measured on the built car: the incidence (pitch) error in degrees
    and any fore/aft position error in mm. `incidence_uncertainty_frac` turns the
    angle error into the same kind of fractional C_l band the tolerance budget
    produces, so a tunnel point that looks "off" can first be checked against "is this
    just the weld-induced misalignment?" before the CFD takes the blame.
    """
    incidence_error_deg: float = 0.0     # as-built pitch/AoA error of the element
    position_error_mm: float = 0.0       # fore/aft mounting position error
    # high-load FSAE elements run on a steep part of the C_l-vs-alpha line; ~0.08 per
    # degree as a fraction of a ~2.5 C_l operating point is a defensible order marker.
    cl_per_deg_frac: float = 0.08
    note: str = ""

    def incidence_uncertainty_frac(self) -> float:
        """Fractional C_l uncertainty from the as-built incidence error."""
        return abs(self.incidence_error_deg) * self.cl_per_deg_frac

    def status(self) -> str:
        if abs(self.incidence_error_deg) < 1e-6 and abs(self.position_error_mm) < 1e-6:
            return ("mount alignment nominal (or unmeasured — confirm the part sits "
                    "where the CAD says before trusting a coefficient)")
        return (f"as-built mount: incidence {self.incidence_error_deg:+.2f} deg "
                f"-> C_l +-{self.incidence_uncertainty_frac()*100:.1f}%, "
                f"position {self.position_error_mm:+.1f} mm. "
                f"Reminder: finalise Dzus holes only AFTER welding so this stays small.")


# --------------------------------------------------------------------------- #
#  The planned scaled run — ties similitude + tolerance + mount into provenance
# --------------------------------------------------------------------------- #
@dataclass
class ScaledRunPlan:
    """
    One planned scaled-model run, bundling the three things that decide whether its
    measured coefficient is allowed to stand in for the full car: the similitude
    plan, the build-tolerance budget, and the as-built mount alignment. Its job is to
    emit a single honest `provenance` string a `TunnelProvenance` (and through it a
    `PhysicalAeroMap`) can carry, plus a combined coefficient uncertainty that folds
    the build tolerance and the mount misalignment together.

    This is the seam back into the existing aero package: build the plan here, then
    pass `plan.tunnel_reynolds()` and `plan.provenance()` straight into the
    `TunnelProvenance(model_scale=..., reynolds=..., notes=...)` the Virtual Wind
    Tunnel already consumes — so a scaled run and a full-size CFD run never get
    silently compared as equals.
    """
    similitude: SimilitudePlan
    tolerance: ToleranceBudget
    mount: MountAlignment = field(default_factory=MountAlignment)

    def tunnel_reynolds(self) -> float:
        return self.similitude.achieved_reynolds

    def model_scale(self) -> float:
        return self.similitude.spec.ratio

    def combined_cl_uncertainty_frac(self) -> float:
        """Build tolerance and mount incidence error, combined in quadrature."""
        tol = self.tolerance.report().cl_uncertainty_frac
        mnt = self.mount.incidence_uncertainty_frac()
        return math.sqrt(tol * tol + mnt * mnt)

    def provenance(self) -> str:
        """The line that keeps a scaled coefficient honest downstream."""
        tol = self.tolerance.report()
        return (f"SCALED MODEL — {self.similitude.provenance_note()}; "
                f"build +-{tol.cl_uncertainty_frac*100:.1f}% C_l / "
                f"+-{tol.cd_uncertainty_frac*100:.1f}% C_d; "
                f"mount incidence +-{self.mount.incidence_uncertainty_frac()*100:.1f}% C_l; "
                f"combined +-{self.combined_cl_uncertainty_frac()*100:.1f}% C_l. "
                f"Not a full-size measurement.")

    def report(self) -> str:
        return (
            "Scaled-model run plan\n"
            "=====================\n"
            f"  {self.similitude.spec.full_size_label()}\n"
            f"  similitude : {self.similitude.verdict}\n"
            f"  run speed  : {self.similitude.achievable_speed_ms:.1f} m/s "
            f"(model Re={self.similitude.achieved_reynolds:.2e})\n"
            f"  tolerance  : {self.tolerance.report().summary}\n"
            f"  mount      : {self.mount.status()}\n"
            f"  combined   : +-{self.combined_cl_uncertainty_frac()*100:.1f}% on C_l "
            f"from build + mount\n"
            f"  provenance : {self.provenance()}"
        )
