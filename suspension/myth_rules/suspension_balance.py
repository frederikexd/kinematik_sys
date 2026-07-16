# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Suspension / vehicle-balance myth rules. Context (optional): a
``VehicleParams`` (or dict with mass/cg_height/track/etc). Checks against the
real load-transfer relationships in ``dynamics`` where numbers are given."""
from __future__ import annotations
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


def _vp(context: Any):
    if context is None:
        return None
    if hasattr(context, "cg_height") and hasattr(context, "track_front"):
        return context
    if isinstance(context, dict):
        return context.get("veh") or context.get("suspension")
    return None


# Stiffer is always better / faster
def _r_stiffer_better(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("stiff", "stiffer", "harder spring", "stiffer spring",
                      "stiffer bar") and
            claim.has("faster", "better", "more grip", "always", "more responsive")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Stiffer is not automatically faster. Springs/bars set how load TRANSFERS, "
         "and because tyre grip is load-sensitive, the axle that gains relatively "
         "more load loses relatively more grip \u2014 so stiffening one end actually "
         "REDUCES that end's grip and shifts balance (stiffer front \u2192 more "
         "understeer). Too stiff overall also stops the tyres following the road, "
         "costing mechanical grip on bumps and kerbs. There's an optimum, and it's a "
         "balance lever, not a 'more = better' knob. Tune it in the GGV/lap sim and "
         "confirm on track."),
        provenance="tyre load-sensitivity \u2192 stiffer end loses grip share")
_r_stiffer_better.reference_claim = "Stiffer springs always make the car faster."


# Lower CG always helps (this one is essentially TRUE, and worth confirming)
def _r_lower_cg(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("lower cg", "lower centre", "lower center", "cg height",
                      "low cg") and
            claim.has("better", "more grip", "helps", "faster", "less transfer")):
        return None
    vp = _vp(context)
    extra = ""
    if vp is not None:
        extra = (f" Your CG is {vp.cg_height:.0f} mm on ~{vp.track_front:.0f} mm "
                 f"track; lateral transfer \u221d CG_height/track, so every mm down "
                 f"directly cuts the grip you lose to transfer.")
    return CheckOutcome(
        Verdict.TRUE,
        ("Correct, and it's one of the few near-universal wins. Lateral load "
         "transfer is proportional to CG height over track width, and because the "
         "tyre loses grip as it's loaded, less transfer = more total axle grip." +
         extra + " Lower the CG wherever the packaging lets you \u2014 battery and "
         "driver mass dominate it on an EV."),
        provenance=("transfer \u221d h/t; uses your params" if extra
                    else "transfer \u221d CG_height/track"))
_r_lower_cg.reference_claim = "A lower centre of gravity helps almost everywhere."


# Anti-roll bar adds grip
def _r_arb_adds_grip(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("anti-roll", "antiroll", "arb", "sway bar", "roll bar") and
            claim.has("grip", "more grip", "adds grip", "increases grip")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("An anti-roll bar doesn't add total grip \u2014 it REDISTRIBUTES it between "
         "axles by changing how much load transfer each end does. Stiffening the "
         "front bar moves grip from front to rear (more understeer) and vice versa. "
         "It's a balance tool. Total grip comes from tyres, CG height, track and "
         "downforce; the bar just decides which end runs out first."),
        provenance="ARB shifts transfer distribution, not total grip")
_r_arb_adds_grip.reference_claim = "A stiffer anti-roll bar adds grip."


RULES = [
    Rule("susp.stiffer_better", "suspension", _r_stiffer_better,
         keywords_any=("stiff", "stiffer", "harder spring"), priority=15),
    Rule("susp.lower_cg", "suspension", _r_lower_cg,
         keywords_any=("lower cg", "lower centre", "lower center", "cg height",
                       "low cg"), priority=25),
    Rule("susp.arb_adds_grip", "suspension", _r_arb_adds_grip,
         keywords_any=("anti-roll", "antiroll", "arb", "sway bar", "roll bar"),
         priority=35),
]
for _rule in RULES:
    register(_rule)
