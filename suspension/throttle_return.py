# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================

"""
Throttle return-spring redundancy — the "must still close if one spring lets go"
gate the brakes/pedal-box lead asked for, plus the brake-pedal 2000 N structural
gate, in one place.

WHY THIS MODULE EXISTS
----------------------
The FSAE throttle rule is not "have a spring". It is a *redundancy* rule: the
throttle actuation system must use **at least two return springs**, positioned so
that the failure of ANY single component of the throttle system — including one
spring coming unhooked — still returns the throttle to the fully closed position.
Sensors (TPS / APPS) explicitly do NOT count as return springs. (Current FSAE /
Formula Student rules, Throttle Actuation; historically the same requirement lived
under the throttle-actuation clause and is unchanged in intent.)

That is exactly the brakes lead's brief, in their words: *"Needs to still bounce
if even one unhooks."* Bounce = return to closed. Even one unhooks = a single
spring failure. So the real design question is not "what spring do we buy" but
"does the system still produce a net closing torque, over the whole pedal travel,
with the worst single spring removed — and with a margin over the friction/stiction
and any push-rod/cable drag that fights the return?"

THE SPRING-CONSTANT QUESTION ("we don't know k")
------------------------------------------------
The lead asked how to figure out the spring constant. KinematiK's honesty
contract (see damper.py, bracket_fos.py, bolted_joint.py) means this module will
NOT invent a k. Instead it gives you the two real ways to get one and marks the
result `is_estimate` until you do:

  1. MEASURE IT (best, five minutes on the bench). Hang a known load on the spring
     and measure deflection. `k_from_deflection()` backs out k = F / x for a linear
     compression/extension spring, or `k_from_two_points()` if you have a preload.
     For a torsion spring, `k_theta_from_torque()` backs out the angular rate from a
     known torque and angle. This is what a design judge wants to see anyway.

  2. COMPUTE IT from the wire (if you know the spring's geometry — e.g. from a
     McMaster / Lee Spring datasheet or by measuring the coil). `k_compression_spring()`
     uses the standard closed-form:  k = G·d⁴ / (8·D³·Na)  (Shigley), where d = wire
     dia, D = mean coil dia, Na = active coils, G = shear modulus of the wire.

Either way the number is *yours* and visible, not a magic default. If you supply
neither, the model still runs on a clearly-flagged representative rate so the
redundancy logic is testable today — but the verdict carries `is_estimate=True`
and says so, the same way the rest of KinematiK does.

WHAT THIS MODULE CHECKS
-----------------------
  * `check_return_redundancy()` — the headline. Given the return springs (each with
    a rate, a moment arm about the pedal pivot, and a preload), the pedal geometry,
    and the resisting torque (friction + cable/rod drag + any sensor detent), it
    computes the NET return torque at the closed stop and at wide-open throttle,
    for the all-springs-healthy case AND for every single-spring-removed case. It
    passes only if EVERY single-failure case still makes net-closing torque with a
    margin over the resistance, across the whole travel. This is the rule, modelled.

  * `check_brake_pedal_2000N()` — the brake-pedal structural gate: the pedal must
    withstand 2000 N applied at the pad without failure. This is a thin, honest
    wrapper over the existing `bracket_fos` screening (same FoS-on-yield rule the
    chassis team already uses) so the brakes lead gets a PASS/TIGHT/FAIL on the
    pedal in the same language as every other bracket — no separate spreadsheet.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not model spring fatigue life, coil clash / solid-height binding as a
transient, or the true 3-D stress state of the pedal (that's the FEA the brake
gate hands off to). It gets you to the right question — "is my return genuinely
single-fault tolerant, and does my pedal clear 2000 N at all" — fast, and is loud
about the parts that still need ANSYS/SolidWorks.

UNITS: SI everywhere in the spring model — N, m, N/m for linear rate, N·m for
torque, radians for angle, N·m/rad for torsional rate. The brake-pedal gate uses
the bracket_fos convention (mm, N, MPa) because it delegates to that module.

REFERENCES: Shigley, *Mechanical Engineering Design* (helical-spring rate,
k = G·d⁴/(8·D³·Na)); FSAE / Formula Student rules, Throttle Actuation
(two independent return springs, single-fault return-to-closed) and Brake System
(brake pedal shall withstand 2000 N without failure).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

from .interfaces import Finding, Severity


# --------------------------------------------------------------------------- #
#  Material shear-modulus library for the wire (only needed for k-from-geometry)
# --------------------------------------------------------------------------- #
#  G (shear modulus) in Pa. These are the spring-wire materials a team actually
#  buys. Values are standard handbook figures; a datasheet G always wins — pass
#  your own if you have it.
WIRE_SHEAR_MODULUS_PA = {
    "music wire (ASTM A228)": 81.7e9,
    "stainless 302/304": 69.0e9,
    "chrome silicon (ASTM A401)": 77.2e9,
    "oil-tempered (ASTM A229)": 79.3e9,
    "phosphor bronze": 41.4e9,
}


# --------------------------------------------------------------------------- #
#  Ways to GET the spring constant (the lead's "how do we figure out k")
# --------------------------------------------------------------------------- #
def k_from_deflection(force_N: float, deflection_m: float) -> float:
    """Back out a LINEAR spring rate from one measured (force, deflection) point.

    k = F / x.  Hang a known load (a labelled gym plate, a luggage scale pulling
    to a mark) on the spring, measure how far it moves, done. This is the honest,
    judge-friendly way to get k for a spring whose datasheet you've lost. Assumes
    the spring is linear and you measured from its free length (no preload); if you
    have a preload use `k_from_two_points`.
    """
    if deflection_m <= 0:
        raise ValueError("deflection must be > 0 to divide by it")
    return float(force_N) / float(deflection_m)


def k_from_two_points(f1_N: float, x1_m: float, f2_N: float, x2_m: float) -> float:
    """Back out a linear rate from TWO (force, position) points — handles preload.

    k = (f2 - f1) / (x2 - x1). Use this when the spring is already seated with some
    preload so you can't measure from free length: take two loads and two positions.
    """
    dx = float(x2_m) - float(x1_m)
    if dx == 0:
        raise ValueError("the two positions must differ")
    return (float(f2_N) - float(f1_N)) / dx


def k_theta_from_torque(torque_Nm: float, angle_rad: float) -> float:
    """Back out an angular (torsion) spring rate: k_theta = T / theta [N·m/rad].

    For a torsion return spring, apply a known torque (a known force at a known
    arm) and measure the wind-up angle. Same idea as `k_from_deflection`, rotational.
    """
    if angle_rad <= 0:
        raise ValueError("angle must be > 0 to divide by it")
    return float(torque_Nm) / float(angle_rad)


def k_compression_spring(wire_dia_m: float, mean_coil_dia_m: float,
                         active_coils: float,
                         material: str = "music wire (ASTM A228)",
                         shear_modulus_Pa: Optional[float] = None) -> float:
    """Closed-form helical compression/extension spring rate (Shigley).

        k = G · d⁴ / (8 · D³ · Na)

    d  = wire diameter (m)          D  = mean coil diameter (m)
    Na = number of ACTIVE coils     G  = wire shear modulus (Pa)

    Use this when you know the spring's geometry (datasheet, or measure it: wire
    dia with a caliper, mean coil dia = OD − d, count the coils). Pass your own
    `shear_modulus_Pa` if you have it; otherwise it comes from the wire library.
    Note Na (active coils) is less than the total turns — subtract the dead end
    coils (typically ~2 for squared-and-ground ends).
    """
    d = float(wire_dia_m)
    D = float(mean_coil_dia_m)
    Na = float(active_coils)
    if d <= 0 or D <= 0 or Na <= 0:
        raise ValueError("wire dia, coil dia and active coils must all be > 0")
    G = shear_modulus_Pa
    if G is None:
        G = WIRE_SHEAR_MODULUS_PA.get(material)
        if G is None:
            raise ValueError(
                f"unknown wire material '{material}' — pass shear_modulus_Pa or pick "
                f"one of: {', '.join(WIRE_SHEAR_MODULUS_PA)}")
    return G * d**4 / (8.0 * D**3 * Na)


# --------------------------------------------------------------------------- #
#  The return-spring model
# --------------------------------------------------------------------------- #
@dataclass
class ReturnSpring:
    """One throttle return spring, described the way it acts on the pedal.

    A spring's job here is to make a CLOSING torque about the pedal pivot across
    the whole travel. We describe it by the closing torque it makes at the two ends
    of travel — which you get from its rate and how far it's stretched/wound at each
    end — so linear and torsion springs live in the same model.

    Fields (SI):
        name            : label for findings ("primary", "pedal backup", ...)
        torque_closed_Nm: closing torque this spring makes at the fully-CLOSED stop
                          (preload torque — must be > 0 so a released pedal is held
                           shut, not just barely at zero)
        torque_open_Nm  : closing torque this spring makes at WIDE-OPEN throttle
                          (>= torque_closed for a spring being wound/stretched as the
                           pedal opens; this is where it works hardest to shut)
        is_estimate     : True if the torques came from an assumed/representative
                          rate rather than a measured or datasheet k
    Build the two torques from a measured rate with `from_linear_spring` or
    `from_torsion_spring` so you never hand-transcribe the geometry.
    """
    name: str
    torque_closed_Nm: float
    torque_open_Nm: float
    is_estimate: bool = False

    @staticmethod
    def from_linear_spring(name: str, k_N_per_m: float, moment_arm_m: float,
                           preload_stretch_m: float, travel_stretch_m: float,
                           is_estimate: bool = False) -> "ReturnSpring":
        """Build from a LINEAR spring pulling on a pedal arm.

        k_N_per_m        : the rate you measured/computed
        moment_arm_m     : perpendicular distance from pivot to the spring line of
                           action (the lever the spring pulls on)
        preload_stretch_m: how much the spring is stretched at the closed stop
        travel_stretch_m : ADDITIONAL stretch from closed to wide-open
        Force = k·stretch; closing torque = force · arm.
        """
        f_closed = k_N_per_m * preload_stretch_m
        f_open = k_N_per_m * (preload_stretch_m + travel_stretch_m)
        return ReturnSpring(name, f_closed * moment_arm_m, f_open * moment_arm_m,
                            is_estimate=is_estimate)

    @staticmethod
    def from_torsion_spring(name: str, k_theta_Nm_per_rad: float,
                            preload_angle_rad: float, travel_angle_rad: float,
                            is_estimate: bool = False) -> "ReturnSpring":
        """Build from a TORSION spring wound about (or near) the pedal pivot.

        Torque = k_theta · wind-up angle. Closed torque uses the preload angle;
        open torque uses preload + travel.
        """
        t_closed = k_theta_Nm_per_rad * preload_angle_rad
        t_open = k_theta_Nm_per_rad * (preload_angle_rad + travel_angle_rad)
        return ReturnSpring(name, t_closed, t_open, is_estimate=is_estimate)

    def as_dict(self):
        return asdict(self)


@dataclass
class ReturnResistance:
    """Everything that FIGHTS the return, as a resisting torque about the pivot.

    The springs have to beat all of this — with margin — at every point of travel,
    with one spring gone. Be generous here; this is the term that catches a return
    that "works on the bench" but sticks in the car.

    Fields (SI, N·m):
        friction_Nm     : pivot + linkage Coulomb friction (stiction), worst-case
        cable_drag_Nm   : cable/rod routing drag opposing the return
        sensor_detent_Nm: any detent/return-fighting torque from the TPS/APPS body
                          (sensors are NOT springs and can quietly resist)
    """
    friction_Nm: float = 0.0
    cable_drag_Nm: float = 0.0
    sensor_detent_Nm: float = 0.0

    @property
    def total_Nm(self) -> float:
        return abs(self.friction_Nm) + abs(self.cable_drag_Nm) + abs(self.sensor_detent_Nm)

    def as_dict(self):
        return asdict(self)


@dataclass
class ReturnCaseResult:
    """Net closing torque for one configuration (all-healthy or one-spring-removed)."""
    label: str                     # "all springs healthy" / "without 'primary'" ...
    net_closed_Nm: float           # net closing torque at the closed stop
    net_open_Nm: float             # net closing torque at wide-open
    margin: float                  # worst net / resistance  (>=1 target means margin==target)
    closes: bool                   # net closing torque stays > 0 across travel?

    def as_dict(self):
        return asdict(self)


@dataclass
class ReturnRedundancyResult:
    verdict: str                   # PASS / TIGHT / FAIL / INVALID
    n_springs: int
    margin_target: float
    worst_case: str                # label of the governing (worst) single-failure case
    worst_margin: float
    is_estimate: bool
    cases: list = field(default_factory=list)      # list[ReturnCaseResult]
    findings: list = field(default_factory=list)   # list[Finding]
    notes: list = field(default_factory=list)

    def as_dict(self):
        d = dict(
            verdict=self.verdict, n_springs=self.n_springs,
            margin_target=self.margin_target, worst_case=self.worst_case,
            worst_margin=self.worst_margin, is_estimate=self.is_estimate,
            cases=[c.as_dict() for c in self.cases],
            findings=[f.as_dict() for f in self.findings],
            notes=list(self.notes),
        )
        return d


def _net_case(label: str, springs: list, resistance_Nm: float) -> ReturnCaseResult:
    """Net closing torque at both ends of travel for a given set of springs."""
    net_closed = sum(s.torque_closed_Nm for s in springs) - resistance_Nm
    net_open = sum(s.torque_open_Nm for s in springs) - resistance_Nm
    worst = min(net_closed, net_open)
    margin = (worst / resistance_Nm) if resistance_Nm > 0 else math.inf
    return ReturnCaseResult(label=label, net_closed_Nm=net_closed,
                            net_open_Nm=net_open, margin=margin,
                            closes=worst > 0)


def check_return_redundancy(springs: list,
                            resistance: Optional[ReturnResistance] = None,
                            margin_target: float = 1.0,
                            tight_band: float = 0.25) -> ReturnRedundancyResult:
    """Check the two-return-spring, single-fault-tolerant throttle rule.

    springs        : list of ReturnSpring (the rule needs at least 2)
    resistance     : ReturnResistance the springs must overcome (friction, drag,
                     sensor detent). Defaults to zero resistance with a loud note —
                     a zero-resistance check is optimistic; put real numbers in.
    margin_target  : how much MORE closing torque than resistance you require in the
                     worst single-failure case. 1.0 means "net closing torque at
                     least equal to the resistance again after beating it" i.e. the
                     surviving spring(s) clear the resistance with 100% margin. This
                     is a screening target — set it to your team's standard.

    Verdict:
        FAIL    fewer than 2 springs, OR some single-failure case fails to close
                (net closing torque goes <= 0 anywhere in travel) — this is a
                rules failure, the throttle would not return.
        TIGHT   every case closes, but the worst single-failure margin is below
                margin_target·(1+tight_band) — it returns, but with little authority
                over friction/stiction; a sticky pivot in the car could hang it.
        PASS    every single-failure case closes with margin >= target·(1+tight_band).
        INVALID nothing to check.

    The check is deliberately conservative: it evaluates the ALL-HEALTHY case and
    EVERY one-spring-removed case, and the verdict is driven by the WORST of the
    single-failure cases — because the rule is about surviving one failure, not
    about the healthy system.
    """
    if resistance is None:
        resistance = ReturnResistance()
        zero_res_note = True
    else:
        zero_res_note = resistance.total_Nm <= 0

    n = len(springs)
    is_estimate = any(getattr(s, "is_estimate", False) for s in springs)
    R = resistance.total_Nm
    notes: list = []
    findings: list = []

    if n < 2:
        # The rule itself: at least two return springs. One (or zero) is an
        # automatic fail regardless of how strong it is.
        findings.append(Finding(
            "throttle-return-redundancy", Severity.FAIL,
            f"Throttle has {n} return spring(s). FSAE requires AT LEAST TWO, arranged "
            f"so the throttle still returns to closed if any one fails. Sensors (TPS/"
            f"APPS) do not count. Add a second, independent return spring.",
            subsystems=["brakes", "powertrain"],
            detail=dict(n_springs=n, rule="two independent return springs")))
        return ReturnRedundancyResult(
            verdict="FAIL", n_springs=n, margin_target=margin_target,
            worst_case="(insufficient springs)", worst_margin=0.0,
            is_estimate=is_estimate, cases=[], findings=findings,
            notes=["need >= 2 return springs"])

    # All-healthy case (context; not what the verdict keys off).
    cases = [_net_case("all springs healthy", list(springs), R)]

    # Every single-spring-removed case — this is the redundancy test.
    single_fail_cases = []
    for i, s in enumerate(springs):
        remaining = [sp for j, sp in enumerate(springs) if j != i]
        c = _net_case(f"without '{s.name}'", remaining, R)
        cases.append(c)
        single_fail_cases.append(c)

    # Verdict is driven by the worst single-failure case.
    governing = min(single_fail_cases, key=lambda c: c.margin)
    worst_margin = governing.margin
    all_close = all(c.closes for c in single_fail_cases)

    tight_ceiling = margin_target * (1.0 + tight_band)
    if not all_close:
        verdict = "FAIL"
    elif worst_margin < margin_target:
        verdict = "FAIL"
    elif worst_margin < tight_ceiling:
        verdict = "TIGHT"
    else:
        verdict = "PASS"

    est_tag = " (using an ESTIMATED spring rate — measure or datasheet k to confirm)" \
        if is_estimate else ""

    if verdict == "FAIL" and not all_close:
        # The failure the lead named: one unhooks and it no longer bounces shut.
        dead = min(single_fail_cases, key=lambda c: min(c.net_closed_Nm, c.net_open_Nm))
        findings.append(Finding(
            "throttle-return-redundancy", Severity.FAIL,
            f"Throttle does NOT return to closed with one spring failed: {dead.label} "
            f"gives net closing torque {min(dead.net_closed_Nm, dead.net_open_Nm):.2f} N·m "
            f"(<= 0 means it hangs open) against {R:.2f} N·m of resistance{est_tag}. "
            f"This is the rule the pedal-box must pass. Increase the other spring's "
            f"preload/rate, or reduce friction/cable drag, until every single-failure "
            f"case closes with margin.",
            subsystems=["brakes", "powertrain"],
            detail=dict(worst_case=dead.label, net_closed=dead.net_closed_Nm,
                        net_open=dead.net_open_Nm, resistance_Nm=R,
                        estimate=is_estimate)))
    elif verdict == "FAIL":
        findings.append(Finding(
            "throttle-return-redundancy", Severity.FAIL,
            f"Throttle returns with one spring failed but with too little authority: "
            f"worst single-failure margin {worst_margin:.2f} over resistance is below "
            f"the {margin_target:.2f} target ({governing.label}){est_tag}. A sticky "
            f"pivot or a cold, draggy cable could hang it. Add preload/rate or cut "
            f"resistance.",
            subsystems=["brakes", "powertrain"],
            detail=dict(worst_case=governing.label, worst_margin=worst_margin,
                        margin_target=margin_target, resistance_Nm=R,
                        estimate=is_estimate)))
    elif verdict == "TIGHT":
        findings.append(Finding(
            "throttle-return-redundancy", Severity.WARN,
            f"Throttle is single-fault tolerant but TIGHT: worst single-failure case "
            f"({governing.label}) closes with margin {worst_margin:.2f} over resistance, "
            f"just over the {margin_target:.2f} floor{est_tag}. It returns, but there's "
            f"little buffer for in-car stiction the bench test won't show — worth more "
            f"preload or less drag.",
            subsystems=["brakes", "powertrain"],
            detail=dict(worst_case=governing.label, worst_margin=worst_margin,
                        margin_target=margin_target, resistance_Nm=R,
                        estimate=is_estimate)))
    else:
        findings.append(Finding(
            "throttle-return-redundancy", Severity.OK,
            f"Throttle is single-fault tolerant: with any one of {n} springs removed "
            f"it still makes net closing torque across full travel, worst-case margin "
            f"{worst_margin:.2f} over {R:.2f} N·m resistance ({governing.label})"
            f"{est_tag}. Meets the two-independent-return-spring rule.",
            subsystems=["brakes", "powertrain"],
            detail=dict(worst_case=governing.label, worst_margin=worst_margin,
                        margin_target=margin_target, resistance_Nm=R,
                        estimate=is_estimate)))

    if zero_res_note:
        # Zero resistance is the throttle equivalent of screening a pedal in bending
        # only: "closes against zero friction" is not the same as "closes". Do not let
        # it read as a clean pass — demote a would-be PASS to TIGHT and say why, the
        # same conservative rule the brake-pedal screen uses.
        demoted = verdict == "PASS"
        notes.append("Resistance was zero — this is an OPTIMISTIC check. Put real "
                     "friction/stiction and cable-drag numbers in ReturnResistance; a "
                     "return that only just closes against zero drag will hang in the car.")
        findings.append(Finding(
            "throttle-return-resistance-coverage", Severity.WARN,
            "Return checked against ZERO resistance — friction, cable/rod drag and "
            "sensor detent were all left at 0. A return that closes only against zero "
            "drag is the throttle version of a pedal screened in bending alone; it will "
            "hang on real in-car stiction. "
            + ("Result demoted from PASS to TIGHT because a zero-resistance screen must "
               "not read as a validated return. " if demoted else "")
            + "Measure the pivot/cable drag (worst-case, cold) and re-run before you "
              "trust this.",
            subsystems=["brakes", "powertrain"],
            detail=dict(resistance_Nm=R, demoted_from_pass=demoted)))
        if demoted:
            verdict = "TIGHT"
    if is_estimate:
        notes.append("At least one spring rate is an estimate. Back out k on the bench "
                     "with k_from_deflection() (hang a known load, measure travel) or "
                     "from geometry with k_compression_spring(), then re-run.")

    return ReturnRedundancyResult(
        verdict=verdict, n_springs=n, margin_target=margin_target,
        worst_case=governing.label, worst_margin=worst_margin,
        is_estimate=is_estimate, cases=cases, findings=findings, notes=notes)


def return_redundancy_report(res: ReturnRedundancyResult) -> str:
    """One-screen text report for the pedal-box design review."""
    lines = []
    lines.append(f"THROTTLE RETURN-SPRING REDUNDANCY — {res.verdict}")
    lines.append(f"  springs: {res.n_springs}   margin target: {res.margin_target:.2f}"
                 f"   {'[ESTIMATED k]' if res.is_estimate else '[measured/datasheet k]'}")
    lines.append(f"  governing single-failure case: {res.worst_case} "
                 f"(margin {res.worst_margin:.2f})")
    lines.append("  cases (net CLOSING torque, N·m; negative = hangs open):")
    for c in res.cases:
        flag = "closes" if c.closes else "HANGS OPEN"
        lines.append(f"    - {c.label:<28} closed={c.net_closed_Nm:7.2f}  "
                     f"open={c.net_open_Nm:7.2f}  [{flag}]")
    for f in res.findings:
        lines.append(f"  [{f.severity.value.upper()}] {f.message}")
    for n in res.notes:
        lines.append(f"  note: {n}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Brake-pedal 2000 N structural gate (delegates to bracket_fos)
# --------------------------------------------------------------------------- #
#  FSAE Brake System: the brake pedal shall withstand a 2000 N force applied at
#  the pad without failure of the pedal or pedal box. We express the pedal as a
#  levered tab (foot load on an arm, reacted at the pivot / master-cylinder
#  clevis) and screen it with the SAME FoS-on-yield rule the chassis team uses for
#  every other bracket — so the brakes lead gets a verdict in the same language,
#  not a separate spreadsheet. The 2000 N is the RULE load; you may additionally
#  screen at a self-imposed higher case load if your team does.
BRAKE_PEDAL_RULE_LOAD_N = 2000.0


def check_brake_pedal_2000N(width_mm: float, thickness_mm: float,
                            lever_arm_mm: float,
                            material: str = "Steel 1018 CR (cold-rolled)",
                            pivot_bolt_dia_mm: Optional[float] = None,
                            edge_dist_mm: Optional[float] = None,
                            n_bolts: int = 1,
                            weld_leg_mm: Optional[float] = None,
                            weld_length_mm: Optional[float] = None,
                            load_N: float = BRAKE_PEDAL_RULE_LOAD_N,
                            fos_target: float = 1.5,
                            is_estimate: bool = True):
    """Screen a brake pedal against the FSAE 2000 N no-failure rule.

    Thin, honest wrapper over `bracket_fos.screen_bracket`: the pedal is modelled
    as a levered tab carrying `load_N` (default the 2000 N rule load) at the pad,
    a distance `lever_arm_mm` from the pivot. Returns a `bracket_fos.BracketResult`
    with the usual PASS/TIGHT/FAIL verdict on FoS->=-1.5-on-yield, so it renders on
    the integration board exactly like the chassis team's brackets.

    Note this screens the pedal as a bending/shear member — it is a screening
    check, `screening_only=True`, and does not replace the SolidWorks/ANSYS run the
    2000 N rule effectively expects for a real pedal. It fails the obviously
    undersized pedal in five seconds so you don't mesh it.

    COVERAGE HONESTY (the point of screening-before-sim): a real pedal fails in more
    ways than root bending — the pivot lug can bear/tear out, a fabricated pedal's
    welds can shear. If you don't give the pivot-bolt or weld geometry, those modes
    are NOT screened, and a bending-only PASS must not be mistaken for a clean pedal.
    So when a mode is skipped for lack of input, this adds a WARN finding and demotes
    a would-be PASS to TIGHT — the screen stays conservative rather than handing you
    a false green light that ANSYS then has to catch. Supply the pivot/weld geometry
    to screen those modes and clear the warning.

    Materials come from the bracket_fos library (steels + aluminium grades);
    7075-T6 and 6061-T6 are there for the aluminium pedals most teams cut.
    """
    # Local import keeps `import suspension` dependency-free (lazy-init contract).
    from .bracket_fos import Bracket, screen_bracket

    br = Bracket(
        name="brake pedal",
        material=material,
        width_mm=width_mm,
        thickness_mm=thickness_mm,
        P_N=abs(float(load_N)),
        lever_arm_mm=lever_arm_mm,
        hole_dia_mm=pivot_bolt_dia_mm or 0.0,
        edge_dist_mm=edge_dist_mm or 0.0,
        weld_leg_mm=weld_leg_mm or 0.0,
        weld_length_mm=weld_length_mm or 0.0,
        load_is_shear=False,          # foot load bends the pedal about the pivot
        n_bolts=n_bolts,
        is_estimate=is_estimate,
    )
    res = screen_bracket(br, fos_target=fos_target)

    # ---- coverage check: which real pedal modes did we actually screen? -------
    # A bending-only PASS is not the same as "the pedal passes". Be loud about the
    # modes we skipped for want of geometry, and don't let an incomplete input read
    # as a trustworthy PASS — that is exactly the false green light screening exists
    # to prevent.
    screened = {m.mode for m in res.modes}
    missing = []
    if not (pivot_bolt_dia_mm and pivot_bolt_dia_mm > 0):
        missing.append("pivot-bolt bearing")
    if not (pivot_bolt_dia_mm and pivot_bolt_dia_mm > 0
            and edge_dist_mm and edge_dist_mm > 0):
        missing.append("pivot-lug tear-out")
    if not (weld_leg_mm and weld_leg_mm > 0 and weld_length_mm and weld_length_mm > 0):
        missing.append("weld-throat shear (if fabricated/welded)")

    if missing and res.verdict in ("PASS", "TIGHT"):
        # Demote a clean-looking PASS to TIGHT: it passed the modes we ran, but we
        # did not run all the modes a real pedal needs. TIGHT already means "passes,
        # no margin to trust" in this codebase, which is the honest reading here.
        demoted = res.verdict == "PASS"
        res.notes.append(
            "COVERAGE: screened " + ", ".join(sorted(screened)) + ". NOT screened: "
            + "; ".join(missing) + ". This is a bending-governed screen only — supply "
            "the pivot-bolt and weld geometry to screen those modes before you trust "
            "a pass.")
        res.findings.append(Finding(
            "brake-pedal-2000N-coverage", Severity.WARN,
            f"Brake pedal screened at {abs(float(load_N)):.0f} N on {len(screened)} "
            f"mode(s) only; NOT checked: {', '.join(missing)}. "
            + ("Result demoted from PASS to TIGHT because an incomplete screen must "
               "not read as a clean pedal. " if demoted else "")
            + "Add the pivot lug and weld geometry, or take this straight to "
              "SolidWorks/ANSYS for those modes — don't let a bending-only pass be "
              "the last word.",
            subsystems=["brakes"],
            detail=dict(screened=sorted(screened), not_screened=missing,
                        load_N=abs(float(load_N)), demoted_from_pass=demoted)))
        if demoted:
            res.verdict = "TIGHT"
    return res


# --------------------------------------------------------------------------- #
#  Transient return: how fast does the throttle SNAP shut, and does it at all?
# --------------------------------------------------------------------------- #
#  The redundancy check answers "does net closing torque stay positive with one
#  spring gone" — a quasi-static torque balance. It does NOT tell you how long the
#  throttle takes to actually return, nor whether stiction stalls it partway. That
#  is a dynamics question: spin the throttle's rotating inertia against the spring
#  torque minus friction, integrate from wide-open to closed, and time it.
#
#  HONESTY CONTRACT (same as the spring constant): a real return-time needs the
#  throttle system's ROTATING INERTIA about its axis (plate + cable + reflected
#  pedal/linkage mass). Nobody has that number to hand, so we do NOT invent it: it
#  is an explicit input, `estimate_throttle_inertia()` gives an honest first cut
#  from geometry, and the result is flagged `is_estimate` until you supply a
#  measured/CAD value. Uncalibrated, the sim still runs on a clearly-flagged
#  representative inertia so the physics is testable today.
#
#  The physics (angular form of Newton's second law, one rotational DOF):
#       I * theta_ddot = T_spring(theta) - T_coulomb*sign(theta_dot) - T_drag
#  theta = throttle angle, 0 at CLOSED, +theta_open at wide-open. T_spring is the
#  net CLOSING torque from the (surviving) springs, taken from the same closed/open
#  torques the redundancy model uses, linearly interpolated in angle (a linear
#  spring is linear in deflection, hence ~linear in angle over the travel).
#
#  STICTION is modelled honestly: if the spring torque at rest never exceeds the
#  static friction, the throttle does NOT start moving — it hangs. A sim that always
#  reports a finite return-time would hide exactly the failure the lead cares about.


@dataclass
class ThrottleInertia:
    """Rotating inertia of the throttle system about its rotation axis (kg·m²).

    This is the transient equivalent of the spring constant: the number the return-
    time depends on and that must be YOURS, not a default. Build it with
    `estimate_throttle_inertia()` from geometry, or measure it, and set
    `is_estimate=False` once you trust it.
    """
    I_kgm2: float = 5.0e-4          # representative FSAE throttle-shaft inertia
    is_estimate: bool = True
    source: str = "representative"

    def as_dict(self):
        return asdict(self)


def estimate_throttle_inertia(plate_mass_kg: float = 0.02,
                              plate_radius_m: float = 0.020,
                              cable_pedal_mass_kg: float = 0.0,
                              pedal_arm_m: float = 0.0,
                              extra_I_kgm2: float = 0.0) -> ThrottleInertia:
    """First-cut rotating inertia of the throttle system about its axis.

    Honest, geometry-based estimate — NOT a measurement. Models:
      * the throttle plate as a thin rectangular flap rotating about its diameter,
        I_plate ≈ (1/12)·m·(2r)²  = (1/3)·m·r²  (flap of half-width r each side),
      * any pedal/cable mass reflected to the shaft as m·(arm)² (point mass on a
        lever), if you give it,
      * an `extra_I` slot for anything else you know (return-spring drum, sensor
        rotor).
    The result carries `is_estimate=True`. Replace it with a CAD mass-properties or
    a measured value (spin-down test) before trusting a return-time to the ms.
    """
    m = max(float(plate_mass_kg), 0.0)
    r = max(float(plate_radius_m), 0.0)
    I_plate = (1.0 / 3.0) * m * r * r
    I_reflected = max(float(cable_pedal_mass_kg), 0.0) * float(pedal_arm_m) ** 2
    I = I_plate + I_reflected + max(float(extra_I_kgm2), 0.0)
    if I <= 0:
        I = 5.0e-4
    return ThrottleInertia(I_kgm2=I, is_estimate=True, source="geometry estimate")


@dataclass
class SnapResult:
    """Outcome of a transient throttle-return (snap-shut) simulation."""
    returns: bool                  # did it reach closed at all?
    return_time_s: float           # time from release to fully closed (inf if it hangs)
    hung_at_deg: float             # angle it stalled at (0 if it closed)
    peak_speed_rad_s: float        # peak angular velocity during return
    theta_open_deg: float
    is_estimate: bool              # inertia and/or spring rate were estimates
    findings: list = field(default_factory=list)
    trace: list = field(default_factory=list)   # (t, theta_deg, omega_rad_s) samples

    def as_dict(self):
        return dict(returns=self.returns, return_time_s=self.return_time_s,
                    hung_at_deg=self.hung_at_deg, peak_speed_rad_s=self.peak_speed_rad_s,
                    theta_open_deg=self.theta_open_deg, is_estimate=self.is_estimate,
                    findings=[f.as_dict() for f in self.findings],
                    n_trace=len(self.trace))


def _spring_closing_torque_at(theta, theta_open, T_closed, T_open):
    """Net spring closing torque at angle theta (rad), linear between the two ends.

    theta=0 -> T_closed (preload), theta=theta_open -> T_open. Clamped outside.
    """
    if theta_open <= 0:
        return T_closed
    frac = min(max(theta / theta_open, 0.0), 1.0)
    return T_closed + (T_open - T_closed) * frac


# --------------------------------------------------------------------------- #
#  Extra transient physics: backlash, nonlinear cam, aero load on the plate
# --------------------------------------------------------------------------- #
AIR_DENSITY_KGM3 = 1.225        # same convention as suspension/aero (sea-level ISA)


@dataclass
class SnapModel:
    """Optional richer physics for the transient snap, beyond spring + Coulomb.

    Everything defaults to OFF (the plain spring-vs-friction model), so adding this
    changes nothing unless you populate it. Each term is explicit and, where a
    quantity can't be known without a flow bench / CFD, defaults to zero and is
    flagged rather than invented.

    CABLE SLACK / BACKLASH
      backlash_deg : dead band (in plate-angle degrees) at the START of the return
        where the return spring is taking up cable/linkage slack and NOT yet acting
        on the plate. If the spring lives on the pedal side of a slack cable, the
        plate is effectively unsprung through this band — the model applies NO
        spring torque there, only friction/aero, so a plate that would drift open on
        aero load shows it. This is the honest version of "the cable has slop".
      backlash_spring_frac : fraction (0..1) of spring torque that DOES reach the
        plate inside the backlash band (0 = fully disconnected, 1 = no effect).

    NONLINEAR CAM PROFILE
      cam_profile : optional list of (plate_angle_deg, torque_multiplier) points
        describing how the mechanism's mechanical advantage varies through travel.
        The spring closing torque at an angle is multiplied by the interpolated
        multiplier. Default None = linear (multiplier 1 everywhere). Use this for a
        cam/quadrant throttle where the effective torque isn't linear in angle.

    AERO LOAD ON THE PLATE (at speed)
      aero_torque_coeff : the ONLY honest way to include intake aero without a flow
        bench. Aero torque on the plate is modelled as
            T_aero = aero_torque_coeff * q * A_plate * r_plate * f(theta)
        where q = 1/2 rho V^2 is the intake dynamic pressure. The coefficient bundles
        the plate's real (CFD/flow-bench) torque behaviour and is DIMENSIONLESS-ish;
        it defaults to 0.0 (no aero effect) and MUST be supplied from data to mean
        anything. aero_opens_plate: if True the aero torque tends to push the plate
        OPEN (the dangerous case — airflow holding the throttle open against the
        return); if False it helps close it.
      intake_speed_ms, plate_area_m2, plate_radius_m, rho : the q and geometry.
    """
    # backlash
    backlash_deg: float = 0.0
    backlash_spring_frac: float = 0.0
    # nonlinear cam
    cam_profile: Optional[list] = None       # list[(angle_deg, multiplier)]
    # aero
    aero_torque_coeff: float = 0.0
    aero_opens_plate: bool = True
    intake_speed_ms: float = 0.0
    plate_area_m2: float = 0.0
    plate_radius_m: float = 0.0
    rho: float = AIR_DENSITY_KGM3

    def is_active(self) -> bool:
        return (self.backlash_deg > 0 or self.cam_profile is not None
                or (self.aero_torque_coeff != 0 and self.intake_speed_ms > 0))

    def aero_is_unquantified(self) -> bool:
        """True if aero was requested (a speed given) but no coefficient supplied."""
        return self.intake_speed_ms > 0 and self.aero_torque_coeff == 0.0

    def as_dict(self):
        return asdict(self)


def _cam_multiplier(theta_deg: float, profile: Optional[list]) -> float:
    """Interpolate the cam torque-multiplier at a plate angle (deg). 1.0 if no cam."""
    if not profile:
        return 1.0
    pts = sorted((float(a), float(m)) for a, m in profile)
    if theta_deg <= pts[0][0]:
        return pts[0][1]
    if theta_deg >= pts[-1][0]:
        return pts[-1][1]
    for (a0, m0), (a1, m1) in zip(pts, pts[1:]):
        if a0 <= theta_deg <= a1:
            if a1 == a0:
                return m0
            f = (theta_deg - a0) / (a1 - a0)
            return m0 + (m1 - m0) * f
    return 1.0


def _aero_torque(model: Optional[SnapModel], theta, theta_open) -> float:
    """Aero torque on the plate (N·m). Positive = tends to OPEN (toward theta_open).

    T = coeff * q * A * r * f(theta), q = 1/2 rho V^2. f(theta) peaks near part-open
    (a plate that's fully closed or fully open sees less net opening torque than one
    at an angle to the flow); we use sin(2*plate_fraction*pi/2)=sin(plate_fraction*pi)
    as a simple, bounded shape. Zero if no coefficient/speed given.
    """
    if model is None or model.aero_torque_coeff == 0.0 or model.intake_speed_ms <= 0:
        return 0.0
    q = 0.5 * model.rho * model.intake_speed_ms ** 2
    A = max(model.plate_area_m2, 0.0)
    r = max(model.plate_radius_m, 0.0)
    if A <= 0 or r <= 0:
        return 0.0
    frac = min(max(theta / theta_open, 0.0), 1.0) if theta_open > 0 else 0.0
    shape = math.sin(frac * math.pi)          # 0 at closed & open, max mid-travel
    mag = model.aero_torque_coeff * q * A * r * shape
    return mag if model.aero_opens_plate else -mag


def _aero_unquantified_finding(model: "SnapModel") -> Finding:
    """A loud WARN when aero was requested (a speed) but no coefficient was given."""
    return Finding(
        "throttle-snap-aero", Severity.WARN,
        f"Aero load on the plate was requested at {model.intake_speed_ms:.0f} m/s but "
        f"no torque coefficient was supplied, so it was modelled as ZERO — the "
        f"return-time here ignores intake aero. The aero torque on a throttle plate "
        f"needs a flow-bench or CFD coefficient; KinematiK won't invent one. Measure "
        f"it (or get it from CFD) and set aero_torque_coeff to include the effect.",
        subsystems=["brakes", "powertrain"],
        detail=dict(intake_speed_ms=model.intake_speed_ms,
                    aero_torque_coeff=model.aero_torque_coeff))


def simulate_return_snap(springs: list,
                         inertia: Optional[ThrottleInertia] = None,
                         resistance: Optional[ReturnResistance] = None,
                         theta_open_deg: float = 90.0,
                         dt: float = 5.0e-4,
                         t_max: float = 2.0,
                         omega_stall: float = 1.0e-3,
                         model: Optional[SnapModel] = None) -> SnapResult:
    """Integrate the throttle's return from wide-open to closed and time it.

    springs        : the return springs PRESENT for this run. To model "one spring
                     unhooked", pass only the surviving spring(s) — same objects the
                     redundancy check uses. Their closed/open torques set the spring
                     curve vs angle.
    inertia        : ThrottleInertia about the rotation axis. Defaults to a flagged
                     representative value; supply yours via estimate_throttle_inertia
                     or a measurement.
    resistance     : ReturnResistance (friction + cable drag + sensor detent). The
                     friction term is treated as Coulomb (opposes motion, has a
                     static threshold that can stall the return); cable drag + sensor
                     detent add to it.
    theta_open_deg : throttle travel from closed to wide-open (typically ~90°).
    dt, t_max      : fixed RK4-style step and integration limit.
    omega_stall    : speed below which, with no net accelerating torque, we call it
                     stalled (hung).
    model          : optional SnapModel adding cable backlash, a nonlinear cam
                     profile, and/or aero load on the plate. Default None = the plain
                     spring-vs-Coulomb model. Aero without a supplied coefficient is
                     treated as zero and flagged, never invented.

    Returns a SnapResult with the return-time (or inf + the angle it hung at), the
    peak return speed, and typed findings. If the springs can't overcome static
    friction at wide-open, it reports HUNG at theta_open — the time-domain version
    of the redundancy check's "hangs open".
    """
    # Defensive: two optional dataclass args in a row are easy to swap by
    # position. If they came in swapped, fix it rather than fail cryptically.
    if isinstance(inertia, ReturnResistance) and (
            resistance is None or isinstance(resistance, ThrottleInertia)):
        inertia, resistance = resistance, inertia
    if resistance is None:
        resistance = ReturnResistance()
    if inertia is None:
        inertia = ThrottleInertia()

    I = max(float(inertia.I_kgm2), 1e-9)
    theta_open = math.radians(max(float(theta_open_deg), 1e-3))
    T_closed = sum(s.torque_closed_Nm for s in springs)
    T_open = sum(s.torque_open_Nm for s in springs)
    # Coulomb friction magnitude; cable drag + sensor detent are motion-opposing too
    T_fric = abs(resistance.friction_Nm)
    T_drag = abs(resistance.cable_drag_Nm) + abs(resistance.sensor_detent_Nm)
    is_estimate = bool(getattr(inertia, "is_estimate", False)) or \
        any(getattr(s, "is_estimate", False) for s in springs)
    findings: list = []

    backlash_rad = math.radians(max(getattr(model, "backlash_deg", 0.0), 0.0)) \
        if model else 0.0

    def spring_torque(theta):
        """Effective spring closing torque at angle, with cam + backlash applied."""
        Ts = _spring_closing_torque_at(theta, theta_open, T_closed, T_open)
        if model is not None and model.cam_profile:
            Ts *= _cam_multiplier(math.degrees(theta), model.cam_profile)
        # Inside the backlash band (near wide-open, taking up slack), only a fraction
        # of spring torque reaches the plate.
        if backlash_rad > 0 and theta > (theta_open - backlash_rad):
            Ts *= max(min(getattr(model, "backlash_spring_frac", 0.0), 1.0), 0.0)
        return Ts

    # ---- stiction check at rest, wide-open ---------------------------------
    # At wide-open we're inside any backlash band, so the plate may be nearly
    # unsprung. Include aero, which at wide-open with backlash can hold it open.
    T_spring_open = spring_torque(theta_open)
    T_aero_open = _aero_torque(model, theta_open, theta_open)   # +ve opens
    # net torque available to START closing (toward -theta): spring closes, aero (if
    # opening) fights it. Must beat static friction+drag.
    net_start = T_spring_open - (T_aero_open if T_aero_open > 0 else 0.0)
    if net_start <= (T_fric + T_drag):
        _why = "spring closing torque"
        if model is not None and backlash_rad > 0:
            _why = "spring torque through the cable backlash"
        _aero_note = ""
        if model is not None and T_aero_open > 0:
            _aero_note = (f" (aero is holding it open with {T_aero_open:.3f} N·m at "
                          f"{model.intake_speed_ms:.0f} m/s)")
        findings.append(Finding(
            "throttle-snap", Severity.FAIL,
            f"Throttle HANGS at wide-open: {_why} {net_start:.3f} N·m can't overcome "
            f"static friction+drag {T_fric + T_drag:.3f} N·m{_aero_note}, so it never "
            f"starts to return. Time-domain single-fault failure — increase "
            f"preload/rate, cut friction/drag/backlash, or reduce aero hold.",
            subsystems=["brakes", "powertrain"],
            detail=dict(net_start=net_start, T_resist=T_fric + T_drag,
                        T_aero_open=T_aero_open, estimate=is_estimate)))
        if model is not None and model.aero_is_unquantified():
            findings.append(_aero_unquantified_finding(model))
        return SnapResult(
            returns=False, return_time_s=math.inf, hung_at_deg=theta_open_deg,
            peak_speed_rad_s=0.0, theta_open_deg=theta_open_deg,
            is_estimate=is_estimate, findings=findings, trace=[(0.0, theta_open_deg, 0.0)])

    # ---- integrate theta from theta_open down to 0 -------------------------
    def accel(theta, omega):
        Ts = spring_torque(theta)                 # closes (toward -theta)
        Ta = _aero_torque(model, theta, theta_open)   # +ve = opens (toward +theta)
        # Drive torque in +theta direction from spring (-Ts) and aero (+Ta if opening)
        drive = -Ts + Ta
        if abs(omega) > omega_stall:
            # kinetic friction opposes motion (omega<0 closing -> friction +theta)
            fric = (T_fric + T_drag) * (1.0 if omega < 0 else -1.0)
            return (drive + fric) / I
        else:
            static_cap = T_fric + T_drag
            if abs(drive) <= static_cap:
                return 0.0
            return (drive - static_cap * (1.0 if drive > 0 else -1.0)) / I

    theta = theta_open
    omega = 0.0
    t = 0.0
    peak_speed = 0.0
    trace = [(0.0, theta_open_deg, 0.0)]
    stalled = False
    n = int(t_max / max(dt, 1e-6))
    for _ in range(n):
        # RK4 on (theta, omega)
        def deriv(th, om):
            return om, accel(th, om)
        k1t, k1o = deriv(theta, omega)
        k2t, k2o = deriv(theta + 0.5 * dt * k1t, omega + 0.5 * dt * k1o)
        k3t, k3o = deriv(theta + 0.5 * dt * k2t, omega + 0.5 * dt * k2o)
        k4t, k4o = deriv(theta + dt * k3t, omega + dt * k3o)
        theta = theta + (dt / 6.0) * (k1t + 2 * k2t + 2 * k3t + k4t)
        omega = omega + (dt / 6.0) * (k1o + 2 * k2o + 2 * k3o + k4o)
        t += dt
        peak_speed = max(peak_speed, abs(omega))
        if theta <= 0.0:
            theta = 0.0
            trace.append((t, 0.0, omega))
            findings.append(Finding(
                "throttle-snap", Severity.OK,
                f"Throttle returns to closed in {t*1000:.0f} ms"
                + (" (ESTIMATED inertia/rate — confirm with measured values)"
                   if is_estimate else "")
                + f". Peak return speed {peak_speed:.1f} rad/s.",
                subsystems=["brakes", "powertrain"],
                detail=dict(return_time_s=t, peak_speed_rad_s=peak_speed,
                            estimate=is_estimate)))
            if model is not None and model.aero_is_unquantified():
                findings.append(_aero_unquantified_finding(model))
            return SnapResult(
                returns=True, return_time_s=t, hung_at_deg=0.0,
                peak_speed_rad_s=peak_speed, theta_open_deg=theta_open_deg,
                is_estimate=is_estimate, findings=findings, trace=trace)
        # stall detection: crept to near-rest partway and can't restart
        if abs(omega) < omega_stall and accel(theta, 0.0) == 0.0 and theta > 1e-4:
            stalled = True
            break
        if len(trace) < 4000:
            trace.append((t, math.degrees(theta), omega))

    # didn't reach closed within t_max, or stalled partway
    findings.append(Finding(
        "throttle-snap", Severity.FAIL,
        f"Throttle does NOT fully return within {t_max*1000:.0f} ms — "
        + (f"stalled at {math.degrees(theta):.1f}° " if stalled else
           f"still at {math.degrees(theta):.1f}° ")
        + "(friction/drag too high for the spring). Time-domain single-fault failure: "
        "add preload/rate or reduce friction.",
        subsystems=["brakes", "powertrain"],
        detail=dict(hung_at_deg=math.degrees(theta), t_max_s=t_max,
                    estimate=is_estimate)))
    return SnapResult(
        returns=False, return_time_s=math.inf, hung_at_deg=math.degrees(theta),
        peak_speed_rad_s=peak_speed, theta_open_deg=theta_open_deg,
        is_estimate=is_estimate, findings=findings, trace=trace)


def simulate_return_snap_single_failures(springs: list,
                                         inertia: Optional[ThrottleInertia] = None,
                                         resistance: Optional[ReturnResistance] = None,
                                         theta_open_deg: float = 90.0,
                                         **kw) -> dict:
    """Run the snap sim for the all-healthy case AND each single-spring-removed case.

    Returns {label: SnapResult}. This is the transient companion to
    check_return_redundancy: it doesn't just ask "does net torque stay positive" but
    "how fast does it actually shut, and does it shut at all, with one spring gone".
    """
    out = {}
    out["all springs healthy"] = simulate_return_snap(
        list(springs), inertia, resistance, theta_open_deg, **kw)
    for i, s in enumerate(springs):
        remaining = [sp for j, sp in enumerate(springs) if j != i]
        if not remaining:
            continue
        out[f"without '{s.name}'"] = simulate_return_snap(
            remaining, inertia, resistance, theta_open_deg, **kw)
    return out
