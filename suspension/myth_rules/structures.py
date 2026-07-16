# ============================================================================
#  KinematiK — Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""Structures / chassis myth rules. Ties to flex, bolted_joint, bracket_fos,
compliance. Checks stiffness/strength/fatigue distinctions teams routinely
conflate."""
from __future__ import annotations
from typing import Any, Optional
from ..mythbuster import CheckOutcome, ParsedClaim, Rule, Verdict, register


# Stronger = stiffer
def _r_strong_vs_stiff(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("strong", "stronger", "strength") and
            claim.has("stiff", "stiffer", "stiffness", "rigid")):
        return None
    return CheckOutcome(
        Verdict.MYTH,
        ("Strength and stiffness are different properties. STIFFNESS (how much it "
         "deflects under load) is set by geometry and the elastic modulus E; "
         "STRENGTH (when it permanently yields or breaks) is set by the yield/"
         "ultimate stress. All steels share nearly the same E \u2014 so a higher-"
         "strength steel is NOT stiffer, and you can't fix a flexy chassis by picking "
         "a stronger alloy; you change the geometry (tube diameter, triangulation) or "
         "the material family. Size stiffness with the flex/compliance model and "
         "strength with the FoS check \u2014 they're separate requirements."),
        provenance="stiffness\u221dE,geometry; strength\u221d\u03c3_yield; E~const for steels")
_r_strong_vs_stiff.reference_claim = "A stronger chassis is a stiffer chassis."


# More bolts / tighter = stronger joint
def _r_more_torque_stronger(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("bolt", "torque", "tighten", "tighter") and
            claim.has("stronger", "more torque", "tighter is better", "won't fail",
                      "more clamp", "always")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("More bolt torque raises PRELOAD, which is what keeps a joint from slipping "
         "and fatiguing \u2014 up to ~75% of the bolt's proof load. Past that you "
         "yield the bolt and LOSE clamp, or crush the clamped parts. A properly "
         "preloaded joint also barely feels the external load (the clamped stack "
         "carries most of it), which is why correct preload \u2014 not maximum torque "
         "\u2014 prevents fatigue. Compute the target from bolt grade and the clamped "
         "stack with the bolted-joint model; don't just 'crank it tighter'."),
        provenance="preload target ~75% proof; over-torque yields/loses clamp")
_r_more_torque_stronger.reference_claim = "Tighter bolts always make a stronger joint."


# Lighter = weaker / unsafe
def _r_lighter_weaker(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    if not (claim.has("lighter", "lightweight", "remove material", "lighten") and
            claim.has("weaker", "unsafe", "break", "fail", "less safe", "dangerous")):
        return None
    return CheckOutcome(
        Verdict.DEPENDS,
        ("Lighter doesn't have to mean weaker \u2014 that's the whole point of "
         "engineering a part rather than guessing it. Material removed from low-stress "
         "regions (where the FoS is high) costs almost no strength, while strength "
         "lives where the load path concentrates stress. The right move is to compute "
         "the stress/FoS map (bracket-FoS, load-path) and remove mass only where the "
         "margin is large. Blind lightening IS dangerous; targeted lightening guided "
         "by the FoS check is how you get light AND safe."),
        provenance="FoS-guided mass removal; needs bracket_fos / loadpath")
_r_lighter_weaker.reference_claim = "Making a part lighter always makes it weaker."


RULES = [
    Rule("struct.strong_vs_stiff", "chassis", _r_strong_vs_stiff,
         keywords_any=("strong", "stronger", "strength"), priority=10),
    Rule("struct.more_torque_stronger", "chassis", _r_more_torque_stronger,
         keywords_any=("bolt", "torque", "tighten", "tighter"), priority=20),
    Rule("struct.lighter_weaker", "chassis", _r_lighter_weaker,
         keywords_any=("lighter", "lightweight", "remove material", "lighten"),
         priority=30),
]
for _rule in RULES:
    register(_rule)
