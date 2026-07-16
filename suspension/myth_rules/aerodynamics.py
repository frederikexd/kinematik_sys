# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Aerodynamics myth rules. Context (optional): dict with any of
``{"cda": float, "cla": float, "downforce_n_at_v": (N, m/s), "mass_kg": float}``.
Most aero claims are about scaling laws (force ∝ v²), which are answerable
without a CFD result; where a specific number is claimed, the rule checks it
against declared coefficients and says when CFD/wind-tunnel data is needed."""
from __future__ import annotations
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


def _aero(context: Any) -> dict:
    return context if isinstance(context, dict) else {}


# Downforce scales with v^2, not v
def _r_downforce_linear(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("downforce", "drag", "aero force") and
            claim.has("double", "twice", "linear", "proportional to speed",
                      "with speed")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Aerodynamic force scales with the SQUARE of speed (F = \u00bd\u03c1V\u00b2"
         "\u00b7C\u00b7A), not linearly. Double the speed \u2192 4\u00d7 the downforce "
         "AND 4\u00d7 the drag. That's why aero matters far more in fast corners than "
         "slow ones, and why a part that helps at 100 km/h does almost nothing in a "
         "30 km/h hairpin. Evaluate aero gains at the speeds your track actually "
         "spends time at (from the lap sim), not peak speed."),
        provenance="F = \u00bd\u03c1V\u00b2CA \u2192 force \u221d V\u00b2")
_r_downforce_linear.reference_claim = "Double the speed, double the downforce."


# More downforce always = faster
def _r_more_downforce_faster(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("more downforce", "more aero", "bigger wing", "add downforce")
            and claim.has("faster", "quicker", "better", "lower lap")):
        return None
    a = _aero(context)
    extra = ""
    if a.get("cla") and a.get("cda"):
        extra = (f" Your current L/D is about {a['cla']/max(a['cda'],1e-6):.1f}; the "
                 f"question is whether added C\u2097A buys more cornering time than its "
                 f"C_dA costs on the straights.")
    return CheckOutcome(
        Verdict.DEPENDS,
        ("Downforce helps cornering but its drag hurts straight-line speed and "
         "energy use \u2014 which matters on an EV with a fixed pack. It's a lap-time "
         "trade, track-dependent: downforce wins on tight, twisty autocross and can "
         "LOSE on a high-speed track or an energy-limited endurance run." + extra +
         " Resolve it in the lap sim with your real aero map, not by intuition."),
        provenance=("L/D from declared coeffs" if extra else "needs aero map + lap sim"))
_r_more_downforce_faster.reference_claim = "More downforce always means a faster lap."


# Wing in ground effect / floor does nothing
def _r_floor_vs_wing(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("floor", "undertray", "diffuser", "ground effect") and
            claim.has("nothing", "useless", "wing is better", "doesn't", "no point")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("A floor/diffuser is usually the most EFFICIENT downforce on the car: it "
         "makes downforce with far less drag than a wing of equal force because it "
         "works by accelerating flow under a sealed undertray rather than turning "
         "air. Dismissing it gives up the best L/D device you have. Quantify it with "
         "a CFD run or wind-tunnel map before ruling it out."),
        provenance="floor L/D >> wing L/D; verify with CFD/tunnel")
_r_floor_vs_wing.reference_claim = "The floor does nothing; only the wings make downforce."


RULES = [
    Rule("aero.downforce_scaling", "aerodynamics", _r_downforce_linear,
         keywords_any=("downforce", "drag", "aero force"), priority=10),
    Rule("aero.more_downforce_faster", "aerodynamics", _r_more_downforce_faster,
         keywords_any=("more downforce", "more aero", "bigger wing", "add downforce"),
         priority=20),
    Rule("aero.floor_vs_wing", "aerodynamics", _r_floor_vs_wing,
         keywords_any=("floor", "undertray", "diffuser", "ground effect"),
         priority=30),
]
for _rule in RULES:
    register(_rule)
