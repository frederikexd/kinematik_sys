# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_reasoner.py — deterministic general-knowledge fallback for the myth-buster
===============================================================================

Why this exists
---------------
The myth-buster's registered rules are *exact*: each one checks a specific claim
against the live models with real arithmetic. That is the gold standard, but it
only covers the claims someone has written a rule for. Every other assumption a
lead types fell through to a bare "no registered rule could check that", which
reads like the tool is broken even for perfectly reasonable engineering claims.

This module is the safety net UNDER the registered rules. When no exact rule
matches, ``assess()`` runs the claim against a broad, hand-curated knowledge
base of physics, engineering and FSAE relationships and returns a reasoned
verdict — the same kind of substantive answer a knowledgeable lead would give,
produced entirely in Python.

The honesty contract (unchanged)
--------------------------------
    * **No AI, no LLM, no network.** Every answer is deterministic: the same
      claim always yields the same verdict and reasoning. It is all keyword
      routing + encoded domain relationships, which you can read below.
    * **Confidence is earned, not faked.** A claim only gets a hard MYTH/TRUE
      verdict when it maps to a relationship the knowledge base actually
      encodes. Anything vaguer comes back as DEPENDS with the governing physics
      and the tradeoffs named — never a confident guess.
    * **FSAE rule claims are flagged for verification.** The rulebook changes
      yearly; encoded limits are the stable, long-standing ones, and every
      rule-flavoured answer tells the user to confirm against the current
      season's official rulebook rather than trusting a hardcoded number.

Public API
----------
    assess(claim, *, discipline=None) -> ReasonedVerdict | None

``claim`` is the ``ParsedClaim`` the engine already built (so we reuse its
number/unit extraction). Returns ``None`` only when even the general reasoner
has nothing relevant to say — in practice that is rare, because the generic
"engineering tradeoff" responder catches broad claims. The caller maps a
returned verdict onto a ``MythResult``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# We depend only on the parsed-claim shape, not the engine, to avoid import
# cycles. Verdict strings are duplicated as plain literals ("myth"/"true"/
# "depends") so this module never imports the engine.


# --------------------------------------------------------------------------- #
#  Result shape                                                                #
# --------------------------------------------------------------------------- #
@dataclass
class ReasonedVerdict:
    """One reasoned answer from the general knowledge base.

    ``verdict`` is a plain string matching the engine's vocabulary
    ("myth" | "true" | "depends"). ``explanation`` is the plain-language
    reasoning. ``discipline`` and ``provenance`` mirror the engine's MythResult
    fields so the caller can build one directly. ``fsae_rule`` set True appends
    the "verify against the current rulebook" note.
    """
    verdict: str
    explanation: str
    discipline: str = ""
    provenance: str = "General engineering knowledge base (no live model)."
    fsae_rule: bool = False


# --------------------------------------------------------------------------- #
#  Knowledge-base entry                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class _Topic:
    """One encoded relationship.

    ``any_of`` — the claim must contain at least one phrase from EACH inner
    group (an AND of ORs), so a topic only fires when the claim is really about
    it. ``respond`` receives the parsed claim and returns a ReasonedVerdict.
    ``priority`` orders topics; the first matching topic that returns non-None
    wins. Higher priority runs first.
    """
    name: str
    any_of: list[list[str]]
    respond: Callable[["object"], Optional[ReasonedVerdict]]
    discipline: str = ""
    priority: int = 0

    def matches(self, lower: str) -> bool:
        return all(any(p in lower for p in group) for group in self.any_of)


_TOPICS: list[_Topic] = []


def _topic(name, any_of, *, discipline="", priority=0):
    def _wrap(fn):
        _TOPICS.append(_Topic(name=name, any_of=any_of, respond=fn,
                              discipline=discipline, priority=priority))
        return fn
    return _wrap


def _v(verdict, explanation, *, discipline="", fsae_rule=False,
       provenance=None):
    kw = {}
    if provenance is not None:
        kw["provenance"] = provenance
    return ReasonedVerdict(verdict=verdict, explanation=explanation,
                           discipline=discipline, fsae_rule=fsae_rule, **kw)


# ===========================================================================
#  VEHICLE DYNAMICS
# ===========================================================================
@_topic("vd.downforce_always_faster",
        [["downforce", "aero"], ["always", "faster", "quicker", "lap"]],
        discipline="aerodynamics", priority=60)
def _downforce_faster(c):
    return _v(
        "depends",
        "More downforce is not unconditionally faster. Downforce grows grip in "
        "corners but its partner — drag — costs you on the straights, and it "
        "scales with velocity squared, so the balance shifts with the track. On "
        "a tight, low-speed autocross the cornering gain usually dominates and "
        "more wing is faster; on a straight-heavy layout the drag penalty can "
        "make you slower. The honest answer is track- and speed-dependent: "
        "evaluate lap time on the actual course (the Lap Time tab), don't assume "
        "a monotonic 'more is better'.",
        discipline="aerodynamics")


@_topic("vd.load_sensitivity",
        [["load", "vertical", "weight on"], ["grip", "traction", "friction"]],
        discipline="suspension", priority=55)
def _load_sensitivity(c):
    return _v(
        "myth",
        "Grip does not rise proportionally with vertical load. Tyres are "
        "load-sensitive: the friction coefficient falls as normal load rises, "
        "so doubling the load gives noticeably LESS than double the lateral "
        "force. This is exactly why lateral load transfer hurts an axle — the "
        "heavily loaded outer tyre gains less than the unloaded inner tyre "
        "loses. Use the tyre model (Suspension/Tyre tab) for the real curve; "
        "the 'twice the load, twice the grip' intuition is the classic myth.",
        discipline="suspension")


@_topic("vd.stiffer_always_better",
        [["stiffer", "stiff", "spring rate", "roll stiffness"],
         ["faster", "better", "more grip", "handling"]],
        discipline="suspension", priority=45)
def _stiffer_better(c):
    return _v(
        "depends",
        "Stiffer is not automatically better. Raising rate reduces body roll "
        "and pitch and can sharpen response, but it also cuts mechanical grip "
        "over bumps and kerbs and shifts the balance toward whichever end you "
        "stiffened (stiffen the front and you add understeer). The optimum is a "
        "compromise set by track roughness, tyre load-sensitivity and the "
        "balance you want — not a 'more is always better' axis.",
        discipline="suspension")


@_topic("vd.lower_cg",
        [["lower", "low"], ["center of gravity", "centre of gravity", "cg",
                            "cog", "roll centre", "roll center"]],
        discipline="suspension", priority=40)
def _lower_cg(c):
    return _v(
        "true",
        "Lowering the centre of gravity is one of the few near-free wins in "
        "vehicle dynamics: it cuts lateral load transfer for a given track "
        "width and corner, which — because tyres are load-sensitive — raises "
        "total axle grip and reduces roll. The only caveats are packaging, "
        "ground clearance and driveline angles; the dynamics themselves favour "
        "a lower CG almost every time.",
        discipline="suspension")


@_topic("vd.weight_always_bad",
        [["lighter", "less weight", "reduce weight", "lower mass", "heavier"],
         ["faster", "better", "quicker", "grip", "accelerat"]],
        discipline="chassis", priority=42)
def _weight(c):
    return _v(
        "depends",
        "Less mass helps acceleration, braking and tyre life and is almost "
        "always worth chasing — but 'lighter is always faster' skips the "
        "tradeoffs. Cornering grip rises with load (though less than "
        "proportionally, since tyres are load-sensitive), so shedding mass "
        "gains less in sustained corners than it does in the straights and "
        "transitions. And weight cut at the cost of stiffness, reliability or "
        "legal minimums is a net loss. Direction: right. Unconditional: no.",
        discipline="chassis")


@_topic("vd.wider_tyre_more_grip",
        [["wider", "bigger tyre", "bigger tire", "tyre width", "tire width"],
         ["grip", "traction", "faster"]],
        discipline="suspension", priority=40)
def _wider_tyre(c):
    return _v(
        "depends",
        "A wider tyre often grips more, but not for the schoolbook reason. "
        "Classic friction says contact area doesn't change friction force; real "
        "tyres beat that because a bigger contact patch lowers pressure and "
        "temperature per unit area and exploits load-sensitivity, raising the "
        "effective grip. But wider costs mass, aero drag, warm-up and possibly "
        "compound availability, and only helps if you can actually load and "
        "heat it. Conditional win, not a law.",
        discipline="suspension")


@_topic("vd.suspension_architecture",
        [["double wishbone", "wishbone", "macpherson", "mcpherson",
          "trailing arm", "multilink", "multi-link", "swing axle",
          "solid axle", "live axle", "pushrod", "pullrod"],
         ["better", "worse", "best", "than", "vs", "versus", "superior",
          "prefer", "should we", "use"]],
        discipline="suspension", priority=58)
def _suspension_architecture(c):
    lower = getattr(c, "lower", "")
    dw = ("double wishbone" in lower or "wishbone" in lower)
    mac = ("macpherson" in lower or "mcpherson" in lower)
    if dw and mac:
        return _v(
            "depends",
            "Double-wishbone vs MacPherson is a constraint tradeoff, not a "
            "ranking. Double wishbones give the designer near-full control of "
            "camber gain, roll centre and scrub through the corner, which is "
            "why nearly every FSAE and racing car uses them — the kinematics "
            "are simply better. MacPherson is cheaper, lighter in part count "
            "and packages well in a road car with a tall strut tower, but it "
            "compromises camber control under roll. For an FSAE car with room "
            "to package upper and lower arms, double wishbone is the usual "
            "choice; 'better' still depends on your packaging and cost "
            "constraints, so state those and it becomes a clear decision. Use "
            "the Kinematics tab to compare camber curves for your actual "
            "geometry.",
            discipline="suspension")
    return _v(
        "depends",
        "Suspension-architecture 'X is better than Y' claims depend on what you "
        "need from the kinematics: camber control through roll, roll-centre "
        "placement, anti-features, packaging, unsprung mass and cost all trade "
        "against each other. There's no universally best linkage — the right "
        "one is set by your track, tyre and packaging. Compare the actual "
        "camber and roll-centre curves in the Kinematics tab rather than "
        "ranking layouts in the abstract.",
        discipline="suspension")


@_topic("vd.understeer_oversteer",
        [["understeer", "oversteer", "push", "loose"],
         ["safe", "faster", "slower", "better", "grip"]],
        discipline="suspension", priority=35)
def _balance(c):
    return _v(
        "depends",
        "Neither understeer nor oversteer is inherently faster — the quick "
        "setup is the one that puts both axles at their peak slip at the same "
        "time (neutral, or a hair of the balance the driver can exploit). Mild "
        "understeer is more stable and forgiving; a touch of oversteer can "
        "rotate the car on entry. Which is 'better' depends on the corner, the "
        "driver and the tyres, so tune balance to peak both axles rather than "
        "chasing one label.",
        discipline="suspension")


@_topic("vd.more_camber_more_grip",
        [["camber", "negative camber", "more camber"],
         ["grip", "traction", "faster", "better", "more"]],
        discipline="suspension", priority=50)
def _camber(c):
    return _v(
        "depends",
        "More negative camber is not monotonically more grip. Static camber "
        "exists to keep the tyre near-upright under roll and camber gain, so "
        "the outside tyre — the one doing the cornering work — sits flat on its "
        "contact patch at peak load. Too little and the tyre rolls onto its "
        "outer shoulder mid-corner; too much and you lose straight-line braking "
        "and traction grip and overheat the inner shoulder. The optimum is the "
        "value that maximises the tyre's lateral force at YOUR roll angle and "
        "camber curve — read it off the tyre model and the camber-vs-roll plot "
        "in the Kinematics tab, don't just add degrees.",
        discipline="suspension")


@_topic("vd.toe",
        [["toe", "toe-in", "toe out", "toe-out", "toe in"],
         ["grip", "turn", "turn-in", "entry", "stable", "faster", "better",
          "help", "response"]],
        discipline="suspension", priority=50)
def _toe(c):
    lower = getattr(c, "lower", "")
    out = ("toe out" in lower or "toe-out" in lower)
    specifics = (
        "Toe-out at the front sharpens turn-in and helps the car rotate into a "
        "corner, at the cost of straight-line stability and a little tyre scrub "
        "(drag and wear). Toe-in does the opposite — more stable, lazier entry. "
        if out else
        "Front toe trades entry response against straight-line stability: "
        "toe-out sharpens turn-in but adds nervousness and scrub, toe-in calms "
        "the car but dulls entry. ")
    return _v(
        "depends",
        specifics +
        "So 'toe X helps' is only true for the behaviour you're chasing and in "
        "small amounts — a few tenths of a degree is a big change, and every "
        "bit of static toe is scrub drag and heat on the straights. Set it to "
        "the entry/stability balance the driver wants, then check tyre "
        "temperatures across the tread to confirm you haven't overdone it.",
        discipline="suspension")


@_topic("vd.rake_ride_height",
        [["rake", "ride height", "ride-height", "lower the car", "lowering",
          "raise the rear", "nose down"],
         ["faster", "better", "more", "downforce", "grip", "max", "maximum",
          "as low as", "always"]],
        discipline="aerodynamics", priority=48)
def _rake_ride_height(c):
    lower = getattr(c, "lower", "")
    # "brakes" contains the substring "rake"; if the ONLY trigger for this topic
    # was that false hit (no real rake/ride-height wording, and the claim is
    # about brakes), decline so it doesn't answer a brakes claim about aero rake.
    _real_rake = re.search(r"\brake\b", lower) is not None
    _height_words = any(w in lower for w in (
        "ride height", "ride-height", "lower the car", "lowering",
        "raise the rear", "nose down"))
    if not _real_rake and not _height_words:
        return None
    return _v(
        "depends",
        "Ride height and rake are strong aero knobs but not 'lower/more is "
        "always better' ones. Lowering the car and adding rake (nose down) "
        "usually grows underbody/diffuser downforce and drops the CG — both "
        "good — but only until the floor stalls, bottoms out, or the platform "
        "gets so stiff or pitch-sensitive that mechanical grip and driver "
        "confidence suffer. The best height is the lowest you can run WITHOUT "
        "the floor grounding under braking/bumps or the aero balance snapping "
        "with pitch. Sweep it against the actual track and suspension travel; "
        "'maximum rake' or 'as low as possible' as an absolute is the myth.",
        discipline="aerodynamics")


@_topic("vd.tyre_pressure",
        [["tyre pressure", "tire pressure", "psi", "bar", "hot pressure",
          "cold pressure", "inflation"],
         ["grip", "faster", "better", "more", "higher", "lower", "traction"]],
        discipline="suspension", priority=52)
def _tyre_pressure(c):
    return _v(
        "depends",
        "Tyre grip vs pressure is a curve with a peak, not a slope — there is "
        "an optimal HOT pressure and both sides of it lose grip. Too low and "
        "the carcass flexes, the tyre overheats and the contact patch "
        "distorts; too high and the patch shrinks and the centre overheats and "
        "goes greasy. 'More/less psi is better' only holds until you cross the "
        "peak. Find it empirically: adjust cold pressures so the tyre reaches "
        "its target hot pressure and even temperatures across the tread, and "
        "read grip off the tyre model at that pressure rather than assuming a "
        "direction.",
        discipline="suspension")


@_topic("vd.damper_stiffness",
        [["damper", "dampers", "shock", "shocks", "rebound", "compression",
          "bump damping"],
         ["stiffer", "softer", "harder", "more", "less", "faster", "better",
          "grip", "comfort"]],
        discipline="suspension", priority=46)
def _dampers(c):
    return _v(
        "depends",
        "Dampers control the RATE of load change, not steady-state load, so "
        "'stiffer/softer dampers = faster' misframes them. Too soft and the "
        "platform floats and transient response goes vague; too stiff and the "
        "tyre skates over bumps and kerbs, losing mechanical grip and upsetting "
        "the contact patch. Unlike springs, damping should be tuned to control "
        "body motions and keep the tyre planted over the actual track surface — "
        "low-speed damping for platform/handling, high-speed for bump "
        "absorption. It's a tuning optimum set by track roughness and the "
        "motions you're controlling, not a stiffness axis where more is better.",
        discipline="suspension")


@_topic("aero.front_wing_size",
        [["front wing", "rear wing", "wing", "splitter", "diffuser",
          "undertray"],
         ["big", "bigger", "biggest", "max", "maximum", "as big", "large",
          "larger", "more", "always"]],
        discipline="aerodynamics", priority=44)
def _wing_size(c):
    return _v(
        "depends",
        "A bigger wing/aero device is not automatically better. More area or "
        "angle grows downforce but also drag (which scales with velocity "
        "squared) and shifts aero BALANCE — a huge front wing that out-loads "
        "the rear gives you snap oversteer, and a device only helps if the car "
        "can actually use the load and the flow stays attached (past a point "
        "the element stalls and you gain drag for nothing). Size aero to the "
        "downforce your tyres can exploit AND to front/rear balance AND to the "
        "drag you can afford on your track — check it on the Lap Time tab. "
        "'As big as possible' is the classic aero myth.",
        discipline="aerodynamics")


# ===========================================================================
#  BRAKES
# ===========================================================================
@_topic("brk.bigger_rotor_stops_faster",
        [["bigger rotor", "larger rotor", "bigger disc", "bigger brake",
          "more braking", "bigger caliper", "bigger calliper"],
         ["stop", "faster", "shorter", "more grip", "deceler"]],
        discipline="brakes", priority=55)
def _bigger_brakes(c):
    return _v(
        "myth",
        "Bigger brakes do not shorten stopping distance on their own. Peak "
        "deceleration is set by tyre grip and load, not caliper size — once you "
        "can lock the wheels (or hit the ABS/limit), more clamping does nothing "
        "for distance. What bigger rotors buy is thermal capacity and "
        "fade-resistance over repeated stops, and pedal feel. For a single "
        "stop, tyres and weight decide it, not brake size.",
        discipline="brakes")


@_topic("brk.brake_bias",
        [["brake bias", "brake balance", "front brake", "rear brake",
          "bias forward", "bias rearward"],
         ["faster", "better", "shorter", "lock", "stable"]],
        discipline="brakes", priority=40)
def _brake_bias(c):
    return _v(
        "depends",
        "Optimal brake bias tracks the dynamic load distribution, which shifts "
        "forward under deceleration. Too much front bias locks the fronts "
        "(understeer, lost steering); too much rear locks the rears "
        "(instability). The fastest bias puts both axles near lock "
        "simultaneously for the given deceleration and grip — it's a tuned "
        "compromise, not a fixed 'more front is safer' rule.",
        discipline="brakes")


# ===========================================================================
#  POWERTRAIN / EV
# ===========================================================================
@_topic("pt.more_power_faster",
        [["more power", "more kw", "more horsepower", "more torque",
          "bigger motor"],
         ["faster", "quicker", "lower lap", "better lap", "win"]],
        discipline="powertrain", priority=45)
def _more_power(c):
    return _v(
        "depends",
        "More power helps only where you can put it down. On an FSAE autocross, "
        "traction, corner exit and driveability often cap usable power well "
        "below peak — extra kW past the traction limit just spins tyres and "
        "adds heat and mass. And the accumulator power draw is capped by rule "
        "(historically 80 kW), so 'more power' may not even be legal. Gains are "
        "real on longer straights, marginal in tight sections.",
        discipline="powertrain", fsae_rule=True)


@_topic("pt.power_limit",
        [["accumulator", "tractive", "battery", "power limit", "power cap",
          "kw limit", "80 kw", "80kw", "draw"],
         ["kw", "power", "draw", "limit", "cap", "legal", "allowed", "rule"]],
        discipline="powertrain", priority=70)
def _power_limit(c):
    kw = c.numbers.get("kw")
    lower = getattr(c, "lower", "")
    # Only answer as a rules point when the claim is really about pack power
    # draw, not any sentence that merely contains the word "power".
    if not any(k in lower for k in ("accumulator", "tractive", "battery",
                                    "power limit", "power cap", "kw limit",
                                    "80 kw", "80kw", "draw")):
        return None

    CAP = 80.0  # kW — historical FSAE Electric accumulator draw cap.
    base = (
        "FSAE Electric has long capped the power drawn from the accumulator at "
        "80 kW, enforced by the energy meter — you cannot legally exceed it "
        "regardless of what the motor could deliver.")
    tail = (" Treat this as the stable limit, but confirm the exact figure and "
            "enforcement in the current season's rulebook before relying on it.")

    # The verdict has to track the NUMBER the user stated. A stated draw ABOVE
    # the cap is a myth ("we can run 90 kW") — the rule forbids it — not a
    # 'true' with a soft caveat. This is exactly the number-vs-rule mistake the
    # myth-buster exists to catch, so it must not read green.
    if kw is not None:
        # Is the claim asserting the car can DRAW/RUN this number, vs merely
        # quoting the cap itself ("the cap is 80 kW")? Assertive verbs make an
        # over-cap figure a compliance myth.
        asserts_draw = any(w in lower for w in (
            "run", "draw", "pull", "make", "produce", "deliver", "push",
            "put out", "use", "we can", "can run", "up to", "peak", "at "))
        # If the user explicitly says they LIMIT/CLAMP the draw, or calls the
        # number a rating (motor/inverter), they already understand the cap —
        # that's the rating-vs-actual-draw case (depends), not a myth.
        knows_limit = any(w in lower for w in (
            "limit", "limited", "clamp", "cap the", "capped", "rated",
            "rating", "electronically", "software limit"))
        if kw > CAP + 0.5:
            if asserts_draw and not knows_limit:
                return _v(
                    "myth",
                    base + f" Your figure of {kw:g} kW is ABOVE the 80 kW cap, so "
                    f"a car actually drawing {kw:g} kW from the accumulator would "
                    "be non-compliant — the energy meter would flag it and you'd "
                    "be black-flagged or fail scrutineering. The motor may be "
                    "rated higher, but the legal, usable ceiling at the "
                    f"accumulator is 80 kW, which is {kw - CAP:g} kW below your "
                    "number." + tail,
                    discipline="powertrain", fsae_rule=True)
            return _v(
                "depends",
                base + f" You've quoted {kw:g} kW, which is above that 80 kW cap. "
                "If that's a motor/inverter rating it can be fine as long as the "
                "power actually pulled from the accumulator is electronically "
                f"limited to 80 kW; if it's the real pack draw, {kw:g} kW is "
                "non-compliant." + tail,
                discipline="powertrain", fsae_rule=True)
        # At or under the cap: the stated number is legal.
        margin = CAP - kw
        within = (f" Your figure of {kw:g} kW is within the 80 kW cap"
                  + (f" (a {margin:g} kW margin)." if margin > 0 else
                     " — right at the limit, so watch transient overshoot."))
        return _v(
            "true", base + within + tail,
            discipline="powertrain", fsae_rule=True)

    # No number stated — just confirm the rule exists, verdict 'true' as before.
    return _v(
        "true", base + tail,
        discipline="powertrain", fsae_rule=True)


@_topic("pt.regen",
        [["regen", "regeneration", "recuper"],
         ["free", "always", "faster", "range", "energy", "better"]],
        discipline="powertrain", priority=35)
def _regen(c):
    return _v(
        "depends",
        "Regen recovers braking energy and helps endurance energy budget, but "
        "it isn't free lap time: it adds control complexity, shifts brake bias "
        "(the tyres still do most of the stopping), and is subject to rule "
        "limits on regen at low speed. Worth it for the energy score; not a "
        "straight speed upgrade.",
        discipline="powertrain", fsae_rule=True)


# ===========================================================================
#  STRUCTURES / MATERIALS
# ===========================================================================
@_topic("str.stronger_stiffer",
        [["stronger", "strength"], ["stiffer", "stiff", "stiffness", "rigid"]],
        discipline="chassis", priority=60)
def _strength_vs_stiffness(c):
    return _v(
        "myth",
        "Strength and stiffness are different properties and don't move "
        "together. Stiffness resists deflection (set by geometry and elastic "
        "modulus E); strength resists failure (set by yield/ultimate stress). "
        "You can have a stiff part that's brittle, or a strong part that flexes "
        "a lot. Steel and aluminium differ ~3x in both density and modulus, so "
        "an 'equally stiff' aluminium part is bulkier but can weigh less — "
        "conflating the two is a common and costly design error.",
        discipline="chassis")


@_topic("str.thicker_stronger",
        [["thicker", "more material", "add material", "bigger tube",
          "thicker wall"],
         ["stronger", "stiffer", "better", "safe"]],
        discipline="chassis", priority=40)
def _thicker(c):
    return _v(
        "depends",
        "Adding material usually raises stiffness and strength, but where you "
        "add it dominates how much. Bending stiffness scales with the second "
        "moment of area, so moving material away from the neutral axis (larger "
        "diameter, thinner wall) is far more mass-efficient than simply "
        "thickening a wall. 'Thicker = better' ignores that a bigger, thinner "
        "section often beats a small, thick one at lower weight — the FSAE "
        "chassis game is stiffness per kilogram, not raw thickness.",
        discipline="chassis")


@_topic("str.carbon_always_better",
        [["carbon", "composite", "carbon fibre", "carbon fiber"],
         ["better", "stronger", "lighter", "always", "stiffer"]],
        discipline="chassis", priority=40)
def _carbon(c):
    return _v(
        "depends",
        "Carbon composite offers excellent stiffness- and strength-to-weight, "
        "but 'always better' ignores cost, manufacturing repeatability, "
        "damage-tolerance, joints/inserts and — for FSAE — the extra rules and "
        "testing that apply to composite structures. A well-designed steel "
        "spaceframe can beat a poorly executed composite on cost, schedule and "
        "reliability. Material choice is a systems decision, not a material "
        "ranking.",
        discipline="chassis", fsae_rule=True)


# ===========================================================================
#  THERMAL / COOLING
# ===========================================================================
@_topic("cool.bigger_radiator",
        [["bigger radiator", "larger radiator", "more cooling", "bigger rad"],
         ["cooler", "better", "always", "temperature", "overheat"]],
        discipline="cooling", priority=45)
def _radiator(c):
    return _v(
        "depends",
        "A bigger radiator adds heat-rejection capacity, but cooling is limited "
        "by airflow and temperature difference, not just core area. Past a "
        "point you're adding mass, frontal area and drag for little gain, and a "
        "poorly ducted large core can flow worse than a well-ducted small one. "
        "Fix airflow and ducting first; size the core to the actual heat load "
        "and worst-case ambient, not by 'bigger is cooler'.",
        discipline="cooling")


# ===========================================================================
#  ELECTRICAL
# ===========================================================================
@_topic("elec.higher_voltage",
        [["higher voltage", "more voltage", "raise voltage", "increase voltage"],
         ["faster", "better", "more power", "efficient", "current"]],
        discipline="electrics", priority=40)
def _voltage(c):
    return _v(
        "depends",
        "Higher pack voltage lets you deliver the same power at lower current, "
        "cutting I\u00b2R losses and cable/conductor mass — a real efficiency and "
        "packaging win. But it raises insulation, isolation and safety "
        "requirements, and FSAE caps the maximum tractive-system voltage. So "
        "higher voltage is often the better engineering choice up to the legal "
        "and safety ceiling, not an unconditional 'more is better'.",
        discipline="electrics", fsae_rule=True)


# ===========================================================================
#  GENERIC ENGINEERING RESPONDERS (broadest net, lowest priority)
# ===========================================================================
_COMPARATIVES = ("better", "worse", "faster", "slower", "stronger", "stiffer",
                 "lighter", "heavier", "more", "less", "always", "never",
                 "best", "worst", "increase", "decrease", "improve", "reduce")

# The generic responders are the broadest net, but they must not manufacture a
# confident-sounding answer for a claim that isn't actually about vehicles or
# engineering (that would be false confidence — the one thing this tool must
# never do). A claim only reaches the generic responders if it mentions at
# least one recognisable engineering / vehicle / FSAE term. Everything else
# falls through to an honest UNKNOWN.
_DOMAIN_TERMS = (
    # dynamics
    "grip", "traction", "tyre", "tire", "downforce", "drag", "aero", "lap",
    "corner", "understeer", "oversteer", "balance", "roll", "pitch", "camber",
    "caster", "toe", "slip", "load transfer", "cg", "center of gravity",
    "centre of gravity", "suspension", "spring", "damper", "shock", "arb",
    "anti-roll", "wheelbase", "track width", "ackermann", "steering",
    "wishbone", "macpherson", "pushrod", "pullrod", "motion ratio",
    # brakes
    "brake", "rotor", "disc", "caliper", "calliper", "pedal", "bias",
    "deceleration", "stopping",
    # powertrain / ev
    "power", "torque", "kw", "horsepower", "motor", "engine", "rpm", "gear",
    "accumulator", "battery", "voltage", "current", "cell", "regen", "tractive",
    "inverter", "energy", "efficiency", "drivetrain", "powertrain",
    # structure / materials
    "chassis", "frame", "stiffness", "strength", "stress", "strain", "modulus",
    "aluminium", "aluminum", "steel", "carbon", "composite", "material", "tube",
    "weld", "fatigue", "yield", "mass", "weight", "kg", "unsprung",
    # thermal
    "cooling", "radiator", "coolant", "temperature", "heat", "thermal", "fan",
    "duct", "airflow",
    # general vehicle / FSAE
    "car", "vehicle", "wheel", "speed", "acceleration", "force", "friction",
    "fsae", "formula", "autocross", "endurance", "skidpad", "rule", "legal",
    "cost", "reliability", "setup", "handling", "performance", "wing",
    "diffuser", "splitter", "undertray",
)


def _is_domain_relevant(lower: str) -> bool:
    return any(term in lower for term in _DOMAIN_TERMS)


# Words/shapes that mark a sentence as ASSERTING something (a claim to check)
# rather than ASKING something (a bare query). The catch-all only fires on the
# former, so "what is base speed?" stays UNKNOWN like the original checker.
_ASSERTION_MARKERS = (
    # comparatives / relationals
    "better", "worse", "faster", "slower", "stronger", "stiffer", "lighter",
    "heavier", "more", "less", "increase", "decrease", "improve", "reduce",
    "bigger", "smaller", "higher", "lower", "always", "never", "best", "worst",
    # copulas / equivalences
    " is ", " are ", " means ", " equals ", "=", " gives ", " makes ", " leads ",
    "results in", "causes", " so ", " because ", "the same as", "identical",
    # coupling verbs (belt-and-braces; coupling reasoner already caught most)
    "affect", "affects", "impact", "impacts", "changes", "influences",
    "matters", "no effect", "doesn't", "does not", "won't", "will not",
    "depends", "proportional", "double", "twice", "half",
)

# Bare interrogatives that, with no assertion marker, mean the user is ASKING,
# not claiming. We keep these as UNKNOWN (a question isn't an assumption).
_QUESTION_STARTS = ("what ", "how ", "why ", "when ", "where ", "which ",
                    "who ", "is there", "are there", "can we", "should we",
                    "do we", "does ")


def _looks_like_assertion(lower: str, claim=None) -> bool:
    """True if the claim asserts something checkable, not just asks a question.

    Deterministic. A stated number (from the parsed claim) counts as an
    assertion; so does any comparative/relational/copula marker. A sentence that
    only opens with an interrogative and carries no assertion marker is treated
    as a question and left to UNKNOWN, matching the original checker's behaviour.
    """
    text = (lower or "").strip()
    if not text:
        return False
    has_number = bool(getattr(claim, "all_numbers", None))
    # A stated number is a strong assertion signal even inside a question-ish
    # sentence ("is 90 kW ok?" is really a claim to check), so honour it first.
    if has_number:
        return True
    # An interrogative with NO number is a question, not an assumption — even
    # though "what IS base speed" contains the copula " is ". Check shape first.
    if text.endswith("?") or text.startswith(_QUESTION_STARTS):
        return False
    # Otherwise, an assertion marker (comparative, copula, coupling verb) means
    # the sentence claims something checkable.
    return any(m in lower for m in _ASSERTION_MARKERS)


@_topic("gen.absolute_claim",
        [["always", "never", "guarantee", "impossible", "definitely",
          "no matter", "in all cases", "every time"]],
        priority=10)
def _absolute(c):
    if not _is_domain_relevant(getattr(c, "lower", "")):
        return None
    return _v(
        "depends",
        "Engineering claims with 'always', 'never' or 'guaranteed' are almost "
        "always too strong. Real systems trade off against each other — grip vs "
        "drag, stiffness vs weight, power vs traction, cooling vs mass — so a "
        "change that helps one metric usually costs another, and the net result "
        "depends on the operating point (track, speed, temperature, load). "
        "Name the specific quantities and the operating condition and the "
        "answer usually becomes checkable; as an absolute, treat it with "
        "suspicion.",
        provenance="General engineering principle (tradeoffs dominate absolutes).")


@_topic("gen.comparative_tradeoff",
        [list(_COMPARATIVES)],
        priority=5)
def _comparative(c):
    if not _is_domain_relevant(getattr(c, "lower", "")):
        return None
    return _v(
        "depends",
        "This is a comparative engineering claim, and the honest answer is "
        "'it depends on the constraint that's actually binding'. Almost every "
        "'more X gives more Y' relationship saturates or reverses once a "
        "different limit takes over (traction, thermal, structural, aero drag, "
        "or an FSAE rule). Pin down the specific quantities and the operating "
        "point — then it can be checked against the live models or a hand "
        "calculation instead of argued. If it touches a rules limit, confirm "
        "against the current season's rulebook.",
        provenance="General engineering knowledge base (no live model).")


# --------------------------------------------------------------------------- #
#  Public entry                                                                #
# --------------------------------------------------------------------------- #
_FSAE_NOTE = (" \u26a0\ufe0f This touches an FSAE rules point. Encoded limits are the "
              "stable, long-standing ones \u2014 always confirm the exact figure and "
              "wording against the current season's official rulebook before "
              "relying on it.")


def _attach_sources(out: "ReasonedVerdict", lower: str, *,
                    domain_relevant: bool = True) -> "ReasonedVerdict":
    """Append the 'where to check / read more' block to a verdict's explanation.

    Deterministic and idempotent: the block is only added once, and the sources
    are chosen from the static registry by the claim text + the verdict's own
    discipline. This is what turns "I can't fully settle this" into "…and here
    is where an engineer would go to settle it", which is the behaviour the
    tool promises whenever it can't give a hard answer from a live model.
    """
    try:
        from . import myth_sources as _src
    except Exception:
        return out
    if getattr(out, "_sourced", False):
        return out
    block = _src.source_block(
        lower, discipline=(out.discipline or None),
        domain_relevant=domain_relevant)
    if block:
        out.explanation = out.explanation + block
    # Mark so a second pass (e.g. the engine re-wrapping) doesn't double-append.
    try:
        object.__setattr__(out, "_sourced", True)
    except Exception:
        pass
    return out


def assess(claim, *, discipline: Optional[str] = None) -> Optional[ReasonedVerdict]:
    """Reason about a claim the registered rules couldn't match.

    Order of reasoning (all deterministic, all pure Python):
      1. **Cross-subsystem coupling** — "does A affect B / A doesn't affect B"
         claims are answered from the encoded coupling graph, naming the
         physical path. This is the biggest gap the generic responder left.
      2. **Encoded topics** — the hand-curated physics/FSAE relationships,
         most-specific first (optionally narrowed to the picked discipline).
      3. **Domain-relevant catch-all** — if the claim clearly concerns the car
         but matches no topic, return a substantive "name the quantities and
         here's where to check" DEPENDS rather than a bare miss.

    Every returned verdict carries a "where to check / read more" source block,
    so the user is never left without a next step. Returns ``None`` only for a
    claim that is neither an affects-claim nor domain-relevant (e.g. an empty,
    number-only, or plainly off-topic sentence) — the caller then shows the
    honest UNKNOWN, which itself recommends where to look.
    """
    lower = getattr(claim, "lower", "") or ""
    if not lower.strip():
        return None

    # --- 1. cross-subsystem coupling ("does A affect B?") ----------------- #
    try:
        from . import myth_coupling as _cpl
        cv = _cpl.assess_coupling(lower)
    except Exception:
        cv = None
    if cv is not None:
        out = _v(cv.verdict, cv.explanation, discipline=cv.discipline,
                 provenance="Cross-subsystem coupling model (no live model).")
        return _attach_sources(out, lower)

    # --- 2. encoded topics ----------------------------------------------- #
    # If a discipline was explicitly picked, prefer topics from it, then fall
    # back to the rest — so "Brakes" narrows before the generic responders.
    ordered = sorted(_TOPICS, key=lambda t: t.priority, reverse=True)
    if discipline:
        ordered = ([t for t in ordered if t.discipline == discipline]
                   + [t for t in ordered if t.discipline != discipline])

    for topic in ordered:
        if not topic.matches(lower):
            continue
        try:
            out = topic.respond(claim)
        except Exception:
            continue
        if out is None:
            continue
        if out.fsae_rule and _FSAE_NOTE.strip() not in out.explanation:
            out.explanation = out.explanation + _FSAE_NOTE
        return _attach_sources(out, lower)

    # --- 3. domain-relevant catch-all ------------------------------------ #
    # The claim mentions the car but no topic owns it. Rather than a bare miss,
    # give the honest "I can't settle this in the abstract, here's what to do"
    # answer WITH sources — the experience the tool promises.
    #
    # BUT only for something shaped like an ASSERTION. A bare question ("what is
    # base speed?", "how do dampers work?") isn't an assumption to bust, and the
    # original checker returned UNKNOWN for those — so we don't manufacture a
    # DEPENDS for them. An assertion is signalled by a comparative/relational
    # word, an "is/are/means/=" copula, an absolute, or a stated number.
    if _is_domain_relevant(lower) and _looks_like_assertion(lower, claim):
        out = _v(
            "depends",
            "This is a reasonable engineering question, but it isn't one this "
            "screening tool can settle from the abstract wording alone — the "
            "answer turns on the specific quantities and the operating point "
            "(track, speed, load, temperature). Pin those down and it becomes "
            "checkable against a hand calculation or the live models below. "
            "If it touches a rules limit, confirm against the current season's "
            "rulebook.",
            discipline=discipline or "",
            provenance="General engineering knowledge base (no live model).")
        return _attach_sources(out, lower)

    # Genuinely off-topic: decline. The caller's UNKNOWN message still points
    # the user somewhere sensible.
    return None


def topic_count() -> int:
    """Number of encoded relationships — surfaced in tests/diagnostics."""
    return len(_TOPICS)


def coupling_edge_count() -> int:
    """Number of encoded cross-subsystem coupling edges (diagnostics/tests)."""
    try:
        from . import myth_coupling as _cpl
        return _cpl.edge_count()
    except Exception:
        return 0
