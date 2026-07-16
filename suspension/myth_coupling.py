# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_coupling.py — deterministic cross-subsystem "does X affect Y?" reasoner
============================================================================

Why this exists
---------------
A huge fraction of the assumptions a lead types are not "more X gives more Y"
inside one subsystem — they are *cross-domain independence* claims:

    "more power doesn't affect suspension"
    "the aero package has no effect on the brakes"
    "changing the springs won't touch tyre temperature"
    "battery mass doesn't matter for handling"

These are exactly the arguments that start turf wars between channels, and the
generic "it depends on the binding constraint" responder answers them badly:
it never actually says WHETHER the two things are coupled, which is the whole
question. A knowledgeable chief engineer would answer instantly, naming the
physical path by which one subsystem loads another (or confirming they really
are independent).

This module encodes that: a small, readable graph of how the car's subsystems
physically couple, plus a parser that pulls the two subjects and the polarity
(affects / doesn't affect) out of free text, and returns a reasoned verdict
that names the coupling path — deterministically, in pure Python.

The honesty contract (same as the rest of the myth-buster)
----------------------------------------------------------
    * **No AI, no network, deterministic.** The coupling graph is static data
      you can read below; the same claim always yields the same verdict.
    * **A "myth" here means the physics contradicts the claim.** If a lead says
      A doesn't affect B and the car has a real, well-known load path from A to
      B, that's a MYTH and we name the path. If A and B are genuinely
      first-order independent, "A doesn't affect B" is TRUE.
    * **We only speak where the graph has an edge.** If neither subject is a
      recognised subsystem, this reasoner declines (returns None) and the
      claim falls through to the general reasoner / sources — we do not invent
      a coupling to look clever.

Public API
----------
    assess_coupling(lower_text) -> CouplingVerdict | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
#  Result                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class CouplingVerdict:
    """A reasoned answer to a cross-subsystem 'does X affect Y' claim.

    ``verdict`` is the engine vocabulary ("myth" | "true" | "depends").
    ``explanation`` names the coupling path (or confirms independence).
    ``a`` / ``b`` are the two resolved subsystem ids (for source routing);
    ``discipline`` is the channel the answer most belongs to.
    """
    verdict: str
    explanation: str
    a: str = ""
    b: str = ""
    discipline: str = ""


# --------------------------------------------------------------------------- #
#  Subsystem vocabulary — phrase -> canonical subsystem id                     #
# --------------------------------------------------------------------------- #
# Ordered so multi-word, more-specific phrases match before bare words. Each id
# is one of the car's coupled subsystems (a superset of the discipline ids, with
# a few finer nodes like "mass" and "cg" that are genuinely cross-cutting).
_SUBJECTS: list[tuple[str, list[str]]] = [
    ("power",      ["engine power", "motor power", "more power", "power output",
                    "horsepower", "powertrain", "torque", "tractive", "the motor",
                    "the engine", "drivetrain", "power"]),
    ("aero",       ["downforce", "aero package", "aerodynamics", "the wing",
                    "front wing", "rear wing", "diffuser", "splitter",
                    "undertray", "aero", "drag"]),
    ("suspension", ["suspension geometry", "the suspension", "suspension",
                    "spring rate", "springs", "the springs", "damper",
                    "dampers", "shock", "shocks", "anti-roll bar", "arb",
                    "roll stiffness", "camber", "caster", "toe", "ride height",
                    "kinematics", "motion ratio", "wheel rate"]),
    ("tyres",      ["tyre temperature", "tire temperature", "tyre grip",
                    "tyre wear", "the tyres", "the tires", "tyre", "tire",
                    "grip", "contact patch"]),
    ("brakes",     ["the brakes", "braking", "brake bias", "brake balance",
                    "rotor", "caliper", "calliper", "pedal", "brake", "stopping"]),
    ("cooling",    ["cooling", "radiator", "coolant", "the temperature",
                    "operating temperature", "thermal", "overheat", "heat"]),
    ("electrics",  ["voltage", "the pack voltage", "current", "the bms",
                    "isolation", "wiring", "harness", "electronics", "electrical"]),
    ("chassis",    ["chassis stiffness", "torsional stiffness", "the chassis",
                    "chassis", "frame", "the frame", "stiffness", "structure"]),
    ("mass",       ["total mass", "vehicle mass", "car weight", "overall weight",
                    "battery mass", "the weight", "mass", "weight", "heavier",
                    "lighter"]),
    ("cg",         ["centre of gravity", "center of gravity", "cg height",
                    "cg", "cog"]),
    ("handling",   ["the handling", "handling", "cornering", "balance",
                    "how the car handles", "lap time", "laptime", "the lap"]),
]


def _resolve_subject(lower: str, exclude: Optional[str] = None) -> Optional[tuple[str, int]]:
    """Find the first subsystem phrase in ``lower``; return (id, position).

    ``exclude`` lets the caller find the SECOND distinct subject by skipping the
    id already found. Position is the index of the match so the caller can order
    the two subjects (A before B) the way the sentence reads.
    """
    best: Optional[tuple[str, int]] = None
    for sid, phrases in _SUBJECTS:
        if sid == exclude:
            continue
        for ph in phrases:
            idx = lower.find(ph)
            if idx >= 0:
                if best is None or idx < best[1]:
                    best = (sid, idx)
                break  # first (most-specific) phrase for this id is enough
    return best


# --------------------------------------------------------------------------- #
#  The coupling graph — how subsystems physically load one another             #
# --------------------------------------------------------------------------- #
# Key is a frozenset{A, B} (undirected: "does A affect B" == "does B affect A"
# for the purpose of 'are they coupled'). Value is (coupled, discipline, path):
#   coupled = True  -> there IS a real first-order physical path; independence
#                      claims about this pair are MYTHS.
#   coupled = False -> genuinely independent to first order; independence claims
#                      are TRUE (with the honest caveat that everything couples
#                      at second order through mass/packaging).
#   coupled = None  -> real but conditional/weak; answer DEPENDS.
# ``path`` is the plain-language explanation of the mechanism.
_EDGES: dict[frozenset, tuple[Optional[bool], str, str]] = {
    frozenset({"power", "suspension"}): (
        True, "suspension",
        "More power raises the longitudinal loads the suspension must react: "
        "harder acceleration transfers weight rearward (squat), loads the rear "
        "tyres and the anti-squat geometry, and bigger tractive forces feed "
        "through the links and mounts. It also usually means a heavier "
        "powertrain and a different weight distribution, which reshapes load "
        "transfer and the spring/damper the car wants. Power and suspension are "
        "coupled through longitudinal load transfer and traction — they are not "
        "independent."),
    frozenset({"power", "tyres"}): (
        True, "suspension",
        "Power and the tyres are directly coupled: at corner exit the tyre's "
        "friction circle sets how much drive you can lay down, and power beyond "
        "that limit just spins and heats the tyre. More power means more long" 
        "itudinal energy into the contact patch — grip, temperature and wear "
        "all move with it."),
    frozenset({"power", "brakes"}): (
        None, "brakes",
        "Power itself doesn't clamp the brakes, but the two interact: a faster "
        "car (more power on the straights) arrives at the braking zone quicker, "
        "so the brakes dump more kinetic energy and run hotter, and on an EV "
        "regen shifts the effective brake bias. First-order the brakes are "
        "sized by mass and speed, not engine output, so the link is real but "
        "secondary."),
    frozenset({"power", "cooling"}): (
        True, "cooling",
        "More power means more waste heat — both from the motor/engine and, on "
        "an EV, from the pack and inverter under bigger currents. The cooling "
        "system is sized to the heat load, so raising power directly raises the "
        "cooling demand. They are coupled."),
    frozenset({"aero", "brakes"}): (
        True, "brakes",
        "Aero and brakes are coupled: downforce presses the tyres down, raising "
        "the grip available for braking, so a car with more downforce can "
        "decelerate harder (and the bias may need retuning). Aero drag also "
        "helps slow the car. 'Aero doesn't affect the brakes' misses the "
        "downforce-into-grip path."),
    frozenset({"aero", "suspension"}): (
        True, "suspension",
        "Aero loads the suspension: downforce is reacted through the springs, so "
        "the platform squashes with speed and the ride height, rake and wheel "
        "rates all interact with the aero map. Stiffer springs are often needed "
        "to hold an aero platform. They are coupled through vertical load."),
    frozenset({"aero", "cooling"}): (
        None, "cooling",
        "Aero and cooling share the same air: ducts, inlets and the radiator "
        "steal flow and add drag, and bodywork shape sets what reaches the core. "
        "So they trade against each other — a cleaner aero body can starve the "
        "cooling, and a big inlet costs downforce/drag. Real but a packaging "
        "tradeoff, not a simple 'more of one, more of the other'."),
    frozenset({"aero", "handling"}): (
        True, "aerodynamics",
        "Aero strongly affects handling: downforce adds grip that grows with "
        "speed, and the front/rear aero balance shifts the car's balance with "
        "speed too. A big front wing that out-loads the rear gives high-speed "
        "oversteer. Aero and handling are coupled."),
    frozenset({"mass", "handling"}): (
        True, "chassis",
        "Mass affects handling directly: it sets the load transfer for a given "
        "corner and track width, and because tyres are load-sensitive, more "
        "mass gives less grip per kilo. It also blunts acceleration and "
        "braking. Where the mass sits matters as much as how much — but 'mass "
        "doesn't affect handling' is false."),
    frozenset({"mass", "suspension"}): (
        True, "suspension",
        "Mass sets the loads the suspension carries: sprung mass fixes the "
        "spring/damper rates the car wants and the natural frequencies, and "
        "unsprung mass hurts the tyre's ability to follow the road. Change the "
        "mass and the suspension you want changes. Coupled."),
    frozenset({"cg", "handling"}): (
        True, "suspension",
        "CG height is one of the strongest handling levers: it sets how much "
        "lateral load transfers in a corner, and lower is almost always better "
        "because it cuts that transfer and raises total axle grip. CG and "
        "handling are tightly coupled."),
    frozenset({"chassis", "handling"}): (
        None, "chassis",
        "Chassis stiffness matters up to a point: it must be stiff enough that "
        "the springs and bars — not the frame — control roll stiffness "
        "distribution, so a floppy chassis blurs your balance tuning. Past "
        "'stiff enough' extra stiffness buys little handling and costs weight. "
        "Real, but with diminishing returns."),
    frozenset({"electrics", "handling"}): (
        None, "electrics",
        "The electrics affect handling mainly through mass and its placement "
        "(the accumulator is heavy) and, if fitted, through torque-vectoring or "
        "traction control that shape the balance. The wiring itself doesn't, but "
        "pack mass/position and control strategy do — so it depends on what you "
        "mean by 'the electrics'."),
    frozenset({"brakes", "suspension"}): (
        True, "suspension",
        "Braking loads the suspension: decel transfers weight forward (dive), "
        "loading the front springs and the anti-dive geometry, and the brake "
        "torque reacts through the uprights and links. Bias and suspension "
        "tuning interact. Coupled."),
    frozenset({"cooling", "handling"}): (
        False, "cooling",
        "To first order the cooling system doesn't change how the car handles — "
        "it keeps things from overheating. The only links are second-order: the "
        "mass of coolant/radiator and where the ducts sit affect weight and "
        "aero slightly. As a direct handling lever, cooling is essentially "
        "independent."),
    frozenset({"electrics", "cooling"}): (
        True, "cooling",
        "On an EV the electrics and cooling are coupled: the pack, inverter and "
        "motor all reject heat, and higher currents mean more I\u00b2R heating to "
        "carry away. The cooling loop is sized to that electrical heat load."),
    frozenset({"power", "aero"}): (
        None, "aerodynamics",
        "Power and aero mostly interact through drag: on the straights the "
        "engine has to overcome aero drag, so a draggier aero package needs "
        "more power to reach the same top speed (and downforce buys corner grip "
        "that lets you carry more speed). They're linked through the "
        "power-vs-drag balance rather than one directly changing the other."),
}

# Pairs we consider genuinely first-order independent unless the graph says
# otherwise. Anything NOT in _EDGES and NOT here comes back DEPENDS (honest:
# "there may be a second-order link, name the quantities").
_INDEPENDENT_DEFAULT = {
    frozenset({"electrics", "aero"}),
    frozenset({"electrics", "brakes"}),
    frozenset({"cooling", "brakes"}),
}


# --------------------------------------------------------------------------- #
#  Claim shape detection                                                       #
# --------------------------------------------------------------------------- #
# Negation / independence markers: the claim asserts A does NOT affect B.
_NEG_AFFECT = [
    "doesn't affect", "does not affect", "doesnt affect", "no effect on",
    "has no effect", "not affect", "won't affect", "wont affect",
    "will not affect", "doesn't change", "does not change", "doesnt change",
    "no impact on", "has no impact", "doesn't matter for", "doesnt matter for",
    "does not matter for", "doesn't touch", "won't touch", "wont touch",
    "independent of", "unrelated to", "nothing to do with", "irrelevant to",
    "doesn't influence", "does not influence",
]
# Positive coupling markers: the claim asserts A DOES affect B.
_POS_AFFECT = [
    "affects", "affect", "changes", "influences", "impacts", "impact on",
    "matters for", "has an effect on", "drives", "determines", "couples with",
    "is coupled to", "changes the", "alters",
]


def _polarity(lower: str) -> Optional[str]:
    """Return 'neg' if the claim asserts independence, 'pos' if it asserts a
    coupling, or None if it isn't an affects/independence claim at all."""
    if any(p in lower for p in _NEG_AFFECT):
        return "neg"
    if any(p in lower for p in _POS_AFFECT):
        return "pos"
    return None


# --------------------------------------------------------------------------- #
#  Public entry                                                                #
# --------------------------------------------------------------------------- #
def assess_coupling(lower: str) -> Optional[CouplingVerdict]:
    """Reason about a cross-subsystem 'does A affect B' claim.

    Returns a CouplingVerdict when the claim is an affects/independence claim
    naming two recognisable subsystems; otherwise None (so the caller falls
    through to the general reasoner). Deterministic and offline.
    """
    lower = (lower or "").strip().lower()
    if not lower:
        return None

    polarity = _polarity(lower)
    if polarity is None:
        return None

    first = _resolve_subject(lower)
    if first is None:
        return None
    second = _resolve_subject(lower, exclude=first[0])
    if second is None:
        return None

    a_id, a_pos = first
    b_id, b_pos = second
    # Order A,B by where they appear so the explanation reads naturally.
    if b_pos < a_pos:
        a_id, b_id = b_id, a_id

    key = frozenset({a_id, b_id})
    edge = _EDGES.get(key)

    # --- known coupling edge --------------------------------------------- #
    if edge is not None:
        coupled, disc, path = edge
        if coupled is True:
            if polarity == "neg":
                # "A doesn't affect B" but there's a real path -> MYTH.
                return CouplingVerdict(
                    "myth",
                    f"Not independent. {path}",
                    a=a_id, b=b_id, discipline=disc)
            # "A affects B" and it does -> TRUE.
            return CouplingVerdict(
                "true",
                f"Correct — these are coupled. {path}",
                a=a_id, b=b_id, discipline=disc)
        if coupled is False:
            if polarity == "neg":
                # "A doesn't affect B" and they're independent -> TRUE.
                return CouplingVerdict(
                    "true",
                    f"Broadly right. {path}",
                    a=a_id, b=b_id, discipline=disc)
            # "A affects B" but they're first-order independent -> MYTH.
            return CouplingVerdict(
                "myth",
                f"Overstated. {path}",
                a=a_id, b=b_id, discipline=disc)
        # coupled is None -> conditional/weak link -> DEPENDS either polarity.
        return CouplingVerdict(
            "depends",
            f"It's conditional. {path}",
            a=a_id, b=b_id, discipline=disc)

    # --- explicit independent-by-default pair ---------------------------- #
    if key in _INDEPENDENT_DEFAULT:
        if polarity == "neg":
            return CouplingVerdict(
                "true",
                f"To first order, yes — {a_id} and {b_id} are largely "
                "independent on an FSAE car, coupled only weakly through shared "
                "mass and packaging. Name a specific quantity if you suspect a "
                "second-order link and it becomes checkable.",
                a=a_id, b=b_id, discipline="")
        return CouplingVerdict(
            "depends",
            f"{a_id.title()} and {b_id} are only weakly linked on an FSAE car "
            "(mostly through shared mass and packaging). If you mean a specific "
            "mechanism, name the two quantities and it can be checked; as a "
            "broad 'affects', the first-order link is small.",
            a=a_id, b=b_id, discipline="")

    # --- two recognised subsystems but no encoded edge ------------------- #
    # Don't guess a mechanism we haven't encoded — answer DEPENDS honestly and
    # let the source block point them at both channels.
    return CouplingVerdict(
        "depends",
        f"Whether {a_id} affects {b_id} depends on the specific quantities and "
        "operating point. On a race car almost everything couples eventually "
        "through shared mass, packaging and load transfer, but the strength of "
        f"the {a_id}\u2192{b_id} link isn't something this screening tool can "
        "settle in the abstract. Name the two numbers you mean and check them "
        "in the relevant tool.",
        a=a_id, b=b_id, discipline="")


def subject_ids() -> list[str]:
    """The recognised subsystem ids — surfaced in tests/diagnostics."""
    return [sid for sid, _ in _SUBJECTS]


def edge_count() -> int:
    """Number of encoded coupling edges — surfaced in tests/diagnostics."""
    return len(_EDGES)
