# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_sources.py — deterministic "where to read more / verify this" registry
===========================================================================

Why this exists
---------------
The myth-buster must never bluff. When a claim maps to an encoded relationship
it answers with reasoning; when it *cannot* check or confidently answer, the
honest move is not a shrug — it is to point the user at the authoritative
places an engineer would actually go to settle the question.

This module is that pointer. It holds a small, hand-curated map from a
discipline (and a few cross-cutting topics) to the canonical references a
Formula-SAE lead would open: the standard textbooks, the SAE papers, the
in-app tools that model the thing for real, and the rulebook where a limit is
defined. ``sources_for(...)`` picks the most relevant handful for a claim and
formats them as a short markdown block the caller appends to an explanation.

The honesty contract (same as the rest of the myth-buster)
----------------------------------------------------------
    * **No AI, no network, deterministic.** The registry is a static table.
      The same claim always yields the same source list. Nothing here fetches
      a URL or asks a model — it is data plus keyword routing you can read.
    * **We recommend, we don't fabricate.** Every entry is a real, well-known
      reference (Milliken, Gillespie, Pacejka, SAE, the FSAE rulebook) or an
      actual tool in this app. We never invent a citation, a page number or a
      URL to look authoritative.
    * **In-app tools are named as the primary check.** The whole tool is "the
      hour before ANSYS/SolidWorks/MATLAB", so where KinematiK itself models
      the thing, that tool is listed first — the user can verify immediately.

Public API
----------
    sources_for(lower_text, *, discipline=None, limit=4) -> list[Source]
    format_sources(sources, *, heading=True) -> str
    source_block(lower_text, *, discipline=None, limit=4, heading=True) -> str
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
#  A single reference                                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Source:
    """One place to read more or verify a claim.

    ``kind`` is a short tag used only for grouping/emoji in the rendered block:
    "tool" (an in-app KinematiK view), "book", "paper", "rulebook", or "ref".
    ``title`` is the human name; ``detail`` says what you'll find there. No URL
    field on purpose — the myth-buster is offline and deterministic, and a
    hardcoded link rots; the title is enough for a lead to find the source.
    """
    title: str
    kind: str = "ref"
    detail: str = ""

    def as_line(self) -> str:
        tag = {
            "tool": "\U0001f6e0\ufe0f",   # 🛠️  in-app tool
            "book": "\U0001f4d8",          # 📘  textbook
            "paper": "\U0001f4c4",         # 📄  paper / standard
            "rulebook": "\U0001f4cb",      # 📋  rulebook
            "ref": "\U0001f517",           # 🔗  general reference
        }.get(self.kind, "\U0001f517")
        body = f"**{self.title}**"
        if self.detail:
            body += f" — {self.detail}"
        return f"{tag} {body}"


# Convenience builders keep the tables below readable.
def _tool(title, detail=""):     return Source(title, "tool", detail)
def _book(title, detail=""):     return Source(title, "book", detail)
def _paper(title, detail=""):    return Source(title, "paper", detail)
def _rulebook(title, detail=""): return Source(title, "rulebook", detail)
def _ref(title, detail=""):      return Source(title, "ref", detail)


# --------------------------------------------------------------------------- #
#  Canonical references, by discipline                                        #
# --------------------------------------------------------------------------- #
# The books/papers are the ones every vehicle-dynamics or FSAE reading list
# names; the tools are the actual KinematiK views that model each subsystem, so
# "verify it" points somewhere the user can click right now.
_BY_DISCIPLINE: dict[str, list[Source]] = {
    "suspension": [
        _tool("Kinematics tab", "camber/roll-centre curves for your geometry"),
        _tool("Suspension / Tyre tab", "the fitted tyre model and load sensitivity"),
        _book("Milliken & Milliken, \u201cRace Car Vehicle Dynamics\u201d (SAE)",
              "the standard reference for tyres, load transfer and balance"),
        _book("Gillespie, \u201cFundamentals of Vehicle Dynamics\u201d (SAE)",
              "clear first-principles treatment of handling"),
        _paper("Pacejka, \u201cTyre and Vehicle Dynamics\u201d",
               "the Magic-Formula tyre model this app fits"),
    ],
    "aerodynamics": [
        _tool("Lap Time tab", "does the downforce actually make YOUR lap faster?"),
        _tool("Aero / CFD tools", "downforce, drag and balance for your bodywork"),
        _book("Katz, \u201cRace Car Aerodynamics\u201d",
              "wings, diffusers, ground effect and balance"),
        _paper("SAE aerodynamics papers (FSAE undertray / wing studies)",
               "peer-reviewed sizing and balance data"),
    ],
    "brakes": [
        _tool("Brakes tab", "pedal box, bias and rotor thermal sizing"),
        _book("Limpert, \u201cBrake Design and Safety\u201d (SAE)",
              "bias, thermal capacity, pedal effort from first principles"),
        _book("Milliken & Milliken, \u201cRace Car Vehicle Dynamics\u201d",
              "why tyre grip, not caliper size, sets stopping distance"),
    ],
    "powertrain": [
        _tool("Powertrain / Tractive-System tab",
              "the live motor envelope and the 80 kW accumulator cap"),
        _tool("Lap Time tab", "where power actually converts to lap time"),
        _rulebook("Current-season FS/FSAE rules (EV / powertrain section)",
                  "the enforced power-draw limit and energy-meter rules"),
        _book("Gillespie, \u201cFundamentals of Vehicle Dynamics\u201d",
              "tractive-effort vs traction limit"),
    ],
    "chassis": [
        _tool("Chassis / Structures tools", "stiffness, load paths and FoS"),
        _book("Shigley, \u201cMechanical Engineering Design\u201d",
              "stress vs stiffness, second moment of area, fatigue"),
        _book("Milliken & Milliken, \u201cRace Car Vehicle Dynamics\u201d",
              "why torsional stiffness matters for a race car"),
        _rulebook("Current-season FS/FSAE rules (structural / SES section)",
                  "chassis and material equivalency requirements"),
    ],
    "cooling": [
        _tool("Cooling / Thermal tools", "heat load, radiator sizing, pack thermal"),
        _book("Incropera, \u201cFundamentals of Heat and Mass Transfer\u201d",
              "convection, \u0394T-driven rejection, sizing a core to a heat load"),
    ],
    "electrics": [
        _tool("Electronics / EV Electrical check", "voltage, current, isolation"),
        _rulebook("Current-season FS/FSAE rules (electrical / TS section)",
                  "max tractive-system voltage, isolation, precharge rules"),
        _book("Basic power-electronics text (I\u00b2R losses, isolation)",
              "why higher voltage cuts current and conductor mass"),
    ],
    "tires": [
        _tool("Suspension / Tyre tab", "the fitted Magic-Formula tyre model"),
        _paper("Pacejka, \u201cTyre and Vehicle Dynamics\u201d",
               "the tyre model this app fits"),
        _paper("FSAE Tire Test Consortium (TTC) data",
               "measured tyre curves for your compound"),
    ],
}

# Generic, discipline-agnostic references — the last-resort list when a claim
# is engineering-flavoured but we can't pin a discipline.
_GENERIC: list[Source] = [
    _book("Milliken & Milliken, \u201cRace Car Vehicle Dynamics\u201d (SAE)",
          "the single most-cited FSAE reference"),
    _book("Gillespie, \u201cFundamentals of Vehicle Dynamics\u201d (SAE)",
          "first-principles vehicle dynamics"),
    _ref("Your team's own test data (skidpad, accel, endurance logs)",
         "beats any textbook for YOUR car"),
    _rulebook("Current-season FS/FSAE rulebook",
              "the final word on any legality question"),
]

# When a claim is clearly NOT about the car at all, we still point somewhere —
# but honestly, off our turf.
_OFF_DOMAIN: list[Source] = [
    _ref("A general reference for that subject",
         "this tool only reasons about vehicle / FSAE engineering"),
]


# --------------------------------------------------------------------------- #
#  Topic keyword -> discipline routing (for when no discipline is supplied)    #
# --------------------------------------------------------------------------- #
# Ordered most-specific first. Used only to guess a discipline from the claim
# text when the caller didn't pass one. Mirrors the reasoner's own vocabulary
# so the source block lines up with the answer above it.
_TOPIC_ROUTES: list[tuple[str, list[str]]] = [
    ("tires", ["tyre", "tire", "grip", "traction", "slip angle", "contact patch",
               "compound", "pacejka"]),
    # brakes BEFORE suspension: "brakes" contains the substring "rake", so route
    # the brake channel first to avoid a brakes claim landing on suspension.
    ("brakes", ["brake", "rotor", "disc", "caliper", "calliper", "pedal",
                "bias", "stopping", "deceleration"]),
    ("suspension", ["suspension", "camber", "caster", "toe", "roll centre",
                    "roll center", "spring", "damper", "shock", "arb",
                    "anti-roll", "wishbone", "macpherson", "motion ratio",
                    "ride height", "load transfer", "understeer",
                    "oversteer", "kinematic", "wheel rate"]),
    ("aerodynamics", ["aero", "downforce", "drag", "wing", "diffuser",
                      "splitter", "undertray", "wake", "lift"]),
    ("powertrain", ["power", "torque", "motor", "engine", "rpm", "gear",
                    "accumulator", "tractive", "regen", "drivetrain",
                    "powertrain", "kw", "horsepower", "inverter"]),
    ("electrics", ["voltage", "current", "cell", "battery", "isolation",
                   "precharge", "contactor", "bms", "wiring", "harness",
                   "electrical", "electronics"]),
    ("cooling", ["cooling", "radiator", "coolant", "temperature", "thermal",
                 "heat", "fan", "duct", "airflow", "overheat"]),
    ("chassis", ["chassis", "frame", "stiffness", "strength", "stress",
                 "strain", "modulus", "aluminium", "aluminum", "steel",
                 "carbon", "composite", "tube", "weld", "fatigue", "yield",
                 "bolt", "joint", "bracket", "structure"]),
]


def guess_discipline(lower: str) -> Optional[str]:
    """Best-effort discipline id from claim text, or None. Deterministic."""
    for disc, kws in _TOPIC_ROUTES:
        if any(k in lower for k in kws):
            return disc
    return None


# --------------------------------------------------------------------------- #
#  Public: pick and format sources                                            #
# --------------------------------------------------------------------------- #
def sources_for(lower: str, *, discipline: Optional[str] = None,
                limit: int = 4, domain_relevant: bool = True) -> list[Source]:
    """Return up to ``limit`` references most relevant to this claim.

    ``discipline`` is the caller's hint (the picked channel or the reasoner's
    matched discipline). If absent, we guess from the text. ``domain_relevant``
    False means the claim isn't about the car at all — return the honest
    off-domain pointer instead of pretending a vehicle textbook covers it.
    """
    lower = lower or ""
    if not domain_relevant:
        return list(_OFF_DOMAIN)

    disc = discipline or guess_discipline(lower)
    picked: list[Source] = []
    seen: set[str] = set()

    def _add(seq):
        for s in seq:
            if s.title in seen:
                continue
            seen.add(s.title)
            picked.append(s)
            if len(picked) >= limit:
                return

    if disc and disc in _BY_DISCIPLINE:
        _add(_BY_DISCIPLINE[disc])
    # Cross-domain claims (e.g. "power affects suspension") touch two channels:
    # add the second discipline's tool too so both sides are covered.
    if len(picked) < limit:
        other = guess_discipline(lower)
        if other and other != disc and other in _BY_DISCIPLINE:
            _add(_BY_DISCIPLINE[other][:1])   # just its primary tool
    if len(picked) < limit:
        _add(_GENERIC)
    return picked[:limit]


def format_sources(sources: list[Source], *, heading: bool = True) -> str:
    """Render a source list as a compact markdown block. Empty if no sources."""
    if not sources:
        return ""
    lines = []
    if heading:
        lines.append("\n\n**Where to check / read more:**")
    for s in sources:
        lines.append(f"- {s.as_line()}")
    return "\n".join(lines)


def source_block(lower: str, *, discipline: Optional[str] = None,
                 limit: int = 4, domain_relevant: bool = True,
                 heading: bool = True) -> str:
    """One-call convenience: pick sources for the claim and format them."""
    return format_sources(
        sources_for(lower, discipline=discipline, limit=limit,
                    domain_relevant=domain_relevant),
        heading=heading)
