# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Brakes myth rules. Context (optional): dict with brake-thermal numbers.
Most claims here are about energy and thermal physics that hold regardless of
the specific rotor, with honest pointers to the brake_thermal model for sizing."""
from __future__ import annotations
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


# Bigger rotor = more braking force
def _r_bigger_rotor_force(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("bigger rotor", "larger rotor", "bigger disc", "bigger disk",
                      "rotor size", "rotor")
            and claim.has("more braking", "stops faster", "stop faster", "more force",
                          "shorter stop", "brake harder", "faster", "quicker")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Peak braking is limited by TYRE grip, not rotor size. Once the tyres are at "
         "the friction limit (or ABS/threshold), a bigger rotor can't shorten the "
         "stop. What a bigger/heavier rotor buys is THERMAL capacity \u2014 more mass "
         "and area to absorb and reject heat over repeated stops without fade. So size "
         "rotors for the endurance heat load (use the brake-thermal model), not for "
         "peak deceleration, which your tyres already cap."),
        provenance="decel capped by tyre \u03bc; rotor size = thermal capacity")
_r_bigger_rotor_force.reference_claim = "A bigger brake rotor makes the car stop faster."


# Braking energy / heat
def _r_brake_energy(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("brake", "braking") and
            claim.has("heat", "energy", "temperature", "thermal", "fade")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("Braking converts the car's kinetic energy (\u00bdmv\u00b2) to heat in the "
         "rotors each stop \u2014 so heat scales with the SQUARE of entry speed and "
         "linearly with mass. The sizing question (will it fade over an endurance "
         "stint?) depends on rotor mass, area, cooling and stop frequency, which is "
         "exactly what the brake-thermal model computes. On a regen-braking EV, "
         "energy the motor recovers never reaches the rotors \u2014 size for the heat "
         "that remains AFTER regen, not total. Run the thermal model with your stop "
         "schedule."),
        provenance="Q = \u00bdmv\u00b2 per stop; needs brake_thermal sizing")
_r_brake_energy.reference_claim = "Brake heat is about the same at any speed."


# Brake bias
def _r_brake_bias(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("brake bias", "brake balance", "bias") and
            claim.has("50", "even", "equal", "centre", "center", "middle")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Brake bias is NOT 50/50. Under braking the car pitches forward and load "
         "transfers onto the front tyres, so the fronts can carry more braking force "
         "before locking \u2014 FSAE cars typically run roughly 60\u201370% front. The "
         "exact split depends on CG height, wheelbase and decel (the same load-"
         "transfer physics as cornering). Set it from your weight distribution and "
         "transfer, then fine-tune so front and rear approach lock together."),
        provenance="forward load transfer \u2192 front-biased; ~60-70% front typical")
_r_brake_bias.reference_claim = "Brake bias should be 50/50 front to rear."


# --------------------------------------------------------------------------- #
#  Throttle return-spring redundancy (FSAE two-spring / single-fault rule)      #
# --------------------------------------------------------------------------- #
def _as_return_result(context: Any):
    """Best-effort extraction of a live ReturnRedundancyResult (or the inputs to
    compute one) from whatever the app handed us. Duck-typed on purpose — this
    must never raise on garbage context (the engine relies on that, and so does
    tests/test_throttle_myth.py::test_bad_context_does_not_crash_engine)."""
    try:
        # 1) the result object passed straight through
        if hasattr(context, "verdict") and hasattr(context, "worst_case"):
            return context
        if isinstance(context, dict):
            # 2) {"return_result": ReturnRedundancyResult}
            rr = context.get("return_result")
            if rr is not None and hasattr(rr, "verdict") and hasattr(rr, "worst_case"):
                return rr
            # 3) raw inputs: {"springs": [...], "resistance": ..., "margin_target": ...}
            #    (the discipline unwrap in the engine means a {"brakes": {...}}
            #    bundle arrives here already unwrapped to the inner dict)
            springs = context.get("springs")
            if isinstance(springs, (list, tuple)) and springs:
                from ..throttle_return import check_return_redundancy
                kwargs = {}
                if context.get("resistance") is not None:
                    kwargs["resistance"] = context["resistance"]
                if context.get("margin_target") is not None:
                    kwargs["margin_target"] = context["margin_target"]
                return check_return_redundancy(list(springs), **kwargs)
    except Exception:
        return None
    return None


def _r_throttle_sensor_is_spring(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("sensor", "tps", "apps", "potentiometer")
            and claim.has("spring", "return")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("A throttle position sensor is NOT a return spring and cannot count as one "
         "of the two required return devices. A sensor measures position; it provides "
         "no closing torque — worse, some sensor bodies add a detent/drag torque that "
         "FIGHTS the return (that's the sensor_detent term in ReturnResistance). The "
         "redundancy rule exists so the throttle still closes with any single spring "
         "failed; only a device that produces closing torque on its own satisfies it. "
         "Fit two real springs and run check_return_redundancy with each one removed."),
        provenance="sensor provides zero closing torque; may add detent drag")
_r_throttle_sensor_is_spring.reference_claim = (
    "The throttle position sensor can count as one of the two required return springs.")


def _r_throttle_identical_backup(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("spring", "throttle")
            and claim.has("identical", "backup", "back-up", "back up", "unhook",
                          "one fails", "spring fails", "one breaks", "other is fine",
                          "still returns", "still closes", "redundan")):
        return None

    rr = _as_return_result(context)
    if rr is not None:
        v = str(getattr(rr, "verdict", "")).upper()
        worst = getattr(rr, "worst_case", "")
        margin = getattr(rr, "worst_margin", float("nan"))
        if v == "FAIL":
            return CheckOutcome(
                Verdict.MYTH,
                (f"Checked against the live model: with the worst single failure "
                 f"({worst}) the remaining spring does NOT return the throttle with "
                 f"the required margin (worst margin {margin:.2f}). 'They're identical' "
                 f"is not a redundancy argument — redundancy is proven per single-fault "
                 f"case, and this configuration fails one."),
                provenance=f"live check_return_redundancy: FAIL, worst case {worst}")
        if v == "PASS":
            return CheckOutcome(
                Verdict.TRUE,
                (f"Confirmed against the live model — but note WHY it holds: each "
                 f"surviving spring clears the resistance on its OWN measured torque "
                 f"(worst single-failure margin {margin:.2f}, case {worst}), not "
                 f"because the springs are 'identical'. If either spring's real torque "
                 f"drifts, re-run the single-fault check."),
                provenance=f"live check_return_redundancy: PASS, worst case {worst}")
        if v == "TIGHT":
            return CheckOutcome(
                Verdict.DEPENDS,
                (f"Marginal on the live numbers: every single-failure case closes, but "
                 f"the worst case ({worst}) has only {margin:.2f} margin over "
                 f"friction/stiction — a sticky pivot in the car could hang it. Add "
                 f"spring authority or reduce resistance before calling this safe."),
                provenance=f"live check_return_redundancy: TIGHT, worst case {worst}")
        # INVALID or unrecognised → fall through to the physics answer below.

    return CheckOutcome(
        Verdict.DEPENDS,
        ("'Identical springs' is an assumption, not a check. Redundancy means the "
         "throttle still closes with ANY single spring failed — so verify each "
         "single-fault case: remove each spring in turn and confirm the survivor's "
         "net closing torque beats pivot friction, cable drag and sensor detent with "
         "margin, at both closed and wide-open. Run check_return_redundancy with your "
         "measured spring torques and resistance; two nominally identical springs can "
         "still both be too weak alone."),
        provenance="redundancy is proven per single-fault case, not by symmetry")
_r_throttle_identical_backup.reference_claim = (
    "The two throttle return springs are identical, so if one fails the other is fine.")


RULES = [
    Rule("brakes.throttle_sensor_is_spring", "brakes", _r_throttle_sensor_is_spring,
         keywords_any=("sensor", "tps", "apps", "potentiometer"), priority=4),
    Rule("brakes.throttle_identical_backup", "brakes", _r_throttle_identical_backup,
         keywords_any=("throttle", "return spring", "spring", "backup", "unhook"),
         priority=5),
    Rule("brakes.bigger_rotor_force", "brakes", _r_bigger_rotor_force,
         keywords_any=("bigger rotor", "larger rotor", "bigger disc", "bigger disk",
                       "rotor size", "rotor"), priority=10),
    Rule("brakes.brake_bias", "brakes", _r_brake_bias,
         keywords_any=("brake bias", "brake balance", "bias"), priority=20),
    Rule("brakes.brake_energy", "brakes", _r_brake_energy,
         keywords_any=("brake", "braking"), priority=30),
]
for _rule in RULES:
    register(_rule)
