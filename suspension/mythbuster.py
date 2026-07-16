# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
mythbuster.py — the cross-discipline assumption checker engine
==============================================================

What this is
------------
The powertrain tab already had a myth-buster: type a claim like "capping power
to 80 kW means we can't rev past 7000 rpm" and it answers *against the live
motor numbers*, deterministically, naming the physics. It worked, it shipped,
and it ended a recurring argument in the powertrain channel.

The problem was that it only lived in one discipline and was written as a single
250-line ``if/elif`` chain with rules and regexes hardcoded inline. There are
eight subsystem channels (aerodynamics, brakes, chassis, cooling, data-acq,
electrics, powertrain, suspension), and every one of them has its own recurring
"is this actually true?" arguments. Copy-pasting that if/elif chain eight times
would produce a 2000-line block only its author understands — the opposite of
the goal, which is to get the leads checking each other's assumptions and to
retire the tribal-knowledge spreadsheets.

So this module turns that one proven pattern into a small ENGINE that any
discipline registers rules into, *without touching engine code*. A rule is a
self-contained object that says:
    * which discipline it belongs to,
    * whether it matches a given free-text claim, and
    * given the live context (the real models/numbers), what the verdict is.

The engine owns the parsing, routing, and result formatting; the disciplines own
their rules. A tyre lead adds a tyre rule in ``myth_rules/tires.py``; nobody
edits the engine.

The honesty contract (inherited, non-negotiable)
------------------------------------------------
Same contract as the rest of KinematiK and the original myth-buster:
    * **Deterministic. No AI, no LLM.** Every verdict is pure arithmetic against
      a model or a declared number. The same claim + same context always gives
      the same answer, and you can read exactly why.
    * **Every verdict carries the live numbers** it was computed from, so it is
      checkable and can't be hand-waved away in a meeting.
    * **It never fakes a discipline KinematiK can't model.** A rule that needs
      data the team hasn't supplied returns ``UNKNOWN`` or ``DEPENDS`` with an
      honest reason — it does not invent a number to look confident.

Verdict vocabulary (unchanged, so the existing UI keeps working)
----------------------------------------------------------------
    "myth"     — the claim is false; here's the correct statement + numbers.
    "true"     — the claim is correct; confirmed against the live numbers.
    "depends"  — conditionally true; the binding constraint / missing fact named.
    "unknown"  — no registered rule could check this claim.

Public API
----------
    parse_claim(text) -> ParsedClaim
    Rule                      (base class; subclass or use FunctionRule)
    MythResult                (verdict + explanation + provenance)
    MythEngine                (registry + dispatch)
    register(rule) / register_many(rules)   (module-level default engine)
    check(text, context=...) -> MythResult  (uses the default engine)

Discipline rule-sets live in ``suspension/myth_rules/`` and self-register on
import. ``check()`` imports them lazily the first time it's called, so this
module stays import-light.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Sequence


# --------------------------------------------------------------------------- #
#  Verdicts                                                                    #
# --------------------------------------------------------------------------- #
class Verdict(str, Enum):
    """The four possible answers. Subclassing str keeps ``verdict == "myth"``
    working for the existing UI and tests."""
    MYTH = "myth"
    TRUE = "true"
    DEPENDS = "depends"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
#  Claim parsing — pull numbers + units out of free text                      #
# --------------------------------------------------------------------------- #
# Each entry: canonical unit key -> regex capturing the leading number. Patterns
# are ordered most-specific-first within a key so e.g. "kW" is not eaten by "W".
# The original powertrain checker only knew rpm/kw/nm/kmh/pct/ratio; the other
# seven disciplines need temperatures, voltages, currents, masses, forces,
# g-loads, pressures, lengths, areas, times and frequencies, so the vocabulary
# is unified here once.
_NUMBER = r'(-?\d[\d,]*\.?\d*)'

_UNIT_PATTERNS: list[tuple[str, str]] = [
    # powertrain / rotational
    ("rpm",   rf'{_NUMBER}\s*(?:rpm|r/min|rev(?:olutions)?(?:\s*per\s*min(?:ute)?)?)'),
    ("kw",    rf'{_NUMBER}\s*(?:kW|KW|kilowatt[s]?)'),
    ("hp",    rf'{_NUMBER}\s*(?:hp|horsepower|bhp)'),
    ("nm",    rf'{_NUMBER}\s*(?:N[\u00b7\u22c5\.*\s-]?m|Nm|newton[\s-]?met(?:er|re)[s]?)'),
    # speed
    ("kmh",   rf'{_NUMBER}\s*(?:km/?h|kmh|kph|km\s*per\s*h)'),
    ("mph",   rf'{_NUMBER}\s*(?:mph|miles?\s*per\s*hour)'),
    ("ms",    rf'{_NUMBER}\s*(?:m/s|mps|met(?:er|re)s?\s*per\s*sec(?:ond)?)'),
    # electrical
    ("v",     rf'{_NUMBER}\s*(?:V\b|volt[s]?|VDC|Vdc)'),
    ("a",     rf'{_NUMBER}\s*(?:A\b|amp[s]?|ampere[s]?)'),
    ("ah",    rf'{_NUMBER}\s*(?:Ah|amp[\s-]?hour[s]?)'),
    ("wh",    rf'{_NUMBER}\s*(?:Wh|watt[\s-]?hour[s]?)'),
    ("ohm",   rf'{_NUMBER}\s*(?:ohm[s]?|\u03a9|m\u03a9|milliohm[s]?)'),
    # thermal
    ("degc",  rf'{_NUMBER}\s*(?:\u00b0\s*C|deg(?:rees)?\s*C|celsius|\u00b0C|C\b(?=\s|$|[.,]))'),
    ("watt",  rf'{_NUMBER}\s*(?:W\b|watt[s]?)(?!h)'),
    # mass / force / accel
    ("kg",    rf'{_NUMBER}\s*(?:kg|kilo(?:gram)?[s]?)'),
    ("g_force", rf'{_NUMBER}\s*(?:g\b|G\b|g[\s-]?force|g[\s-]?s)(?!ram)'),
    ("n",     rf'{_NUMBER}\s*(?:N\b|newton[s]?)(?!m)'),
    # pressure
    ("bar",   rf'{_NUMBER}\s*(?:bar)'),
    ("psi",   rf'{_NUMBER}\s*(?:psi)'),
    ("kpa",   rf'{_NUMBER}\s*(?:kPa|kilopascal[s]?)'),
    # geometry
    ("mm",    rf'{_NUMBER}\s*(?:mm|millimet(?:er|re)[s]?)'),
    ("m2",    rf'{_NUMBER}\s*(?:m\^?2|m\u00b2|square\s*met(?:er|re)[s]?)'),
    ("deg",   rf'{_NUMBER}\s*(?:\u00b0|deg(?:rees)?)(?!\s*C)'),
    ("m",     rf'{_NUMBER}\s*(?:m\b|met(?:er|re)[s]?)(?!/|m|\u00b2|\^?2)'),
    # time / frequency
    ("hz",    rf'{_NUMBER}\s*(?:kHz|khz|Hz|hz)'),
    ("sec",   rf'{_NUMBER}\s*(?:s\b|sec(?:ond)?[s]?|ms\b|millisec(?:ond)?[s]?)'),
    # dimensionless
    ("pct",   rf'{_NUMBER}\s*%'),
    ("ratio", rf'{_NUMBER}\s*[:]\s*1'),
]

_COMPILED = [(k, re.compile(p, re.IGNORECASE)) for k, p in _UNIT_PATTERNS]


@dataclass
class ParsedClaim:
    """A parsed free-text claim.

    ``numbers`` maps a canonical unit key (see ``_UNIT_PATTERNS``) to the first
    value found with that unit. ``all_numbers`` keeps every match in order, so a
    rule that needs two rpm figures ("4000 rpm gives 90 km/h") can still get
    them. ``text`` / ``lower`` are the raw and lower-cased claim for keyword
    matching.
    """
    text: str
    lower: str
    numbers: dict[str, float]
    all_numbers: list[tuple[str, float]]

    def has(self, *phrases: str) -> bool:
        """True if ANY of the phrases appears (case-insensitive substring)."""
        return any(p.lower() in self.lower for p in phrases)

    def has_all(self, *phrases: str) -> bool:
        """True if ALL of the phrases appear."""
        return all(p.lower() in self.lower for p in phrases)

    def num(self, key: str, default: Optional[float] = None) -> Optional[float]:
        return self.numbers.get(key, default)


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_claim(text: str) -> ParsedClaim:
    """Extract numbers+units and normalise a free-text claim for rule matching.

    Pure and deterministic. Unit detection is best-effort: a rule should treat a
    missing number as "not stated" (often a DEPENDS/UNKNOWN), never guess.
    """
    raw = (text or "").strip()
    lower = raw.lower()
    numbers: dict[str, float] = {}
    all_numbers: list[tuple[str, float]] = []
    for key, rx in _COMPILED:
        for m in rx.finditer(raw):
            val = _to_float(m.group(1))
            if val is None:
                continue
            all_numbers.append((key, val))
            if key not in numbers:        # keep first occurrence per unit
                numbers[key] = val
    return ParsedClaim(text=raw, lower=lower, numbers=numbers,
                       all_numbers=all_numbers)


# --------------------------------------------------------------------------- #
#  Result                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class MythResult:
    """The answer to one checked claim.

    Backwards-compatible with the original ``AssumptionResult``: it exposes
    ``verdict`` (a str-enum that ``== "myth"`` etc.), ``matched_rule``,
    ``explanation`` and ``user_values``. It adds ``discipline`` (which channel
    owns it) and ``provenance`` (what the verdict was computed from), so a green
    answer never implies more certainty than the data behind it.
    """
    verdict: Verdict
    matched_rule: str
    explanation: str
    discipline: str = ""
    user_values: dict = field(default_factory=dict)
    provenance: str = ""

    # --- compatibility shims so existing UI/tests keep working unchanged ---
    @property
    def correction(self) -> str:        # old MythCheck used `.correction`
        return self.explanation

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict.value if isinstance(self.verdict, Verdict)
            else str(self.verdict),
            "matched_rule": self.matched_rule,
            "explanation": self.explanation,
            "discipline": self.discipline,
            "user_values": dict(self.user_values),
            "provenance": self.provenance,
        }


# --------------------------------------------------------------------------- #
#  Rule protocol                                                               #
# --------------------------------------------------------------------------- #
@dataclass
class Rule:
    """One checkable assumption.

    A rule is intentionally tiny so a discipline lead can write one in a few
    lines without understanding the engine:

        Rule(
            name="tires.more_load_more_grip",
            discipline="suspension",
            keywords_any=["more load", "more grip", "load sensitivity"],
            check=_check_load_sensitivity,   # (claim, context) -> (Verdict, str, provenance)
        )

    Matching: a rule is a *candidate* for a claim if the claim contains at least
    one phrase from ``keywords_any`` AND (if given) at least one from each group
    in ``keywords_all_of``. The engine then calls ``check`` on every candidate
    in ``priority`` order and returns the first that does not decline.

    ``check`` signature:
        check(claim: ParsedClaim, context: Any) -> CheckOutcome | None
    where returning ``None`` means "I matched on keywords but on closer
    inspection this isn't my claim — pass to the next rule." Otherwise return a
    ``CheckOutcome`` (verdict, explanation, optional provenance).
    """
    name: str
    discipline: str
    check: Callable[["ParsedClaim", Any], "Optional[CheckOutcome]"]
    keywords_any: Sequence[str] = field(default_factory=tuple)
    keywords_all_of: Sequence[Sequence[str]] = field(default_factory=tuple)
    priority: int = 100          # lower fires first
    description: str = ""

    def matches(self, claim: ParsedClaim) -> bool:
        if self.keywords_any and not claim.has(*self.keywords_any):
            return False
        for group in self.keywords_all_of:
            if not claim.has(*group):
                return False
        return True


@dataclass
class CheckOutcome:
    """What a rule's ``check`` returns when it fires."""
    verdict: Verdict
    explanation: str
    provenance: str = ""


# Convenience for the common case where a plain function IS the rule body.
def FunctionRule(name: str, discipline: str, *,
                 keywords_any: Sequence[str] = (),
                 keywords_all_of: Sequence[Sequence[str]] = (),
                 priority: int = 100,
                 description: str = "") -> Callable[[Callable], Rule]:
    """Decorator turning a ``check`` function into a registered-ready ``Rule``::

        @FunctionRule("aero.more_downforce_faster", "aerodynamics",
                      keywords_any=["more downforce", "faster"])
        def _rule(claim, ctx):
            return CheckOutcome(Verdict.DEPENDS, "...", provenance="...")
    """
    def _wrap(fn: Callable[[ParsedClaim, Any], Optional[CheckOutcome]]) -> Rule:
        return Rule(name=name, discipline=discipline, check=fn,
                    keywords_any=tuple(keywords_any),
                    keywords_all_of=tuple(tuple(g) for g in keywords_all_of),
                    priority=priority,
                    description=description or (fn.__doc__ or "").strip())
    return _wrap


# --------------------------------------------------------------------------- #
#  Engine                                                                      #
# --------------------------------------------------------------------------- #
class MythEngine:
    """Registry + dispatch. Holds rules; routes a claim to candidates; returns
    the first concrete verdict.

    The engine is deliberately context-agnostic: ``context`` is whatever a rule
    needs (a ``MotorEnvelope``, a tyre model, a ``VehicleParams``, a dict of
    several, or ``None``). Rules document what they expect and degrade to
    DEPENDS/UNKNOWN when it's absent, rather than crashing — so a claim is always
    answerable even with no context wired up.
    """

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    # -- registration --
    def register(self, rule: Rule) -> Rule:
        if any(r.name == rule.name for r in self._rules):
            raise ValueError(f"duplicate myth rule name: {rule.name!r}")
        self._rules.append(rule)
        return rule

    def register_many(self, rules: Iterable[Rule]) -> None:
        for r in rules:
            self.register(r)

    def rules(self, discipline: Optional[str] = None) -> list[Rule]:
        rs = sorted(self._rules, key=lambda r: (r.priority, r.name))
        if discipline:
            rs = [r for r in rs if r.discipline == discipline]
        return rs

    def disciplines(self) -> list[str]:
        return sorted({r.discipline for r in self._rules})

    # -- dispatch --
    def check(self, text: str, context: Any = None) -> MythResult:
        """Check one free-text claim. Returns a ``MythResult`` (never raises for
        an unmatched or un-checkable claim — those come back as UNKNOWN)."""
        claim = parse_claim(text)
        candidates = [r for r in self.rules() if r.matches(claim)]

        declined: list[str] = []
        for rule in candidates:
            try:
                outcome = rule.check(claim, _context_for(rule, context))
            except Exception as exc:   # a buggy rule must not take down the tab
                declined.append(f"{rule.name} errored: {type(exc).__name__}")
                continue
            if outcome is None:
                declined.append(rule.name)
                continue
            return MythResult(
                verdict=outcome.verdict,
                matched_rule=rule.name,
                explanation=outcome.explanation,
                discipline=rule.discipline,
                user_values=dict(claim.numbers),
                provenance=outcome.provenance,
            )

        # Nothing matched (or every candidate declined).
        return MythResult(
            verdict=Verdict.UNKNOWN,
            matched_rule="no_rule_matched",
            explanation=_unknown_message(claim, self, declined),
            discipline="",
            user_values=dict(claim.numbers),
            provenance="",
        )

    def reference_myths(self, discipline: Optional[str] = None,
                        context: Any = None, limit: int = 8) -> list[MythResult]:
        """Return canned 'known myths' for a discipline, evaluated against the
        live context — the reference list the UI shows below the input box. A
        rule opts in by exposing a ``.reference_claim`` attribute (set via
        ``reference_claim=`` on the function) giving an example claim string.
        """
        out: list[MythResult] = []
        for rule in self.rules(discipline):
            example = getattr(rule.check, "reference_claim", None)
            if not example:
                continue
            out.append(self.check(example, context))
            if len(out) >= limit:
                break
        return out


def _context_for(rule: Rule, context: Any) -> Any:
    """Allow ``context`` to be a dict keyed by discipline (so the app can pass a
    bundle), while a rule that wants the whole thing still gets it. If context is
    a dict and contains the rule's discipline, hand the rule that slice;
    otherwise hand it the whole context."""
    if isinstance(context, dict) and rule.discipline in context:
        return context[rule.discipline]
    return context


def _unknown_message(claim: ParsedClaim, engine: "MythEngine",
                     declined: list[str]) -> str:
    """The honest 'couldn't check that' message — never a dead end.

    Even when neither a registered rule nor the general reasoner can answer, we
    don't leave the user with a shrug: we say plainly that this is outside what
    the tool models and point them at where to actually look it up. The source
    block is chosen deterministically from the claim text (the same static
    registry the reasoned answers use), so an off-topic claim still gets a
    sensible next step rather than nothing.
    """
    disc = ", ".join(engine.disciplines()) or "none registered yet"
    base = (
        "No registered rule could check that claim, and it doesn't map onto a "
        "relationship in the built-in physics / engineering / FSAE knowledge "
        "base either. The myth-buster only answers assumptions it can test "
        "against a model or an encoded relationship, so it won't guess. "
    )
    if declined:
        base += ("Some rules recognised keywords but decided the claim wasn't "
                 "theirs. ")
    base += f"Disciplines with rules right now: {disc}."
    # Recommend where to look. If the claim mentions nothing vehicle-related,
    # be honest that it's off this tool's turf rather than pointing at a car
    # textbook for a non-car question.
    try:
        from . import myth_sources as _src
        from . import myth_reasoner as _reason
        lower = getattr(claim, "lower", "") or ""
        _relevant = _reason._is_domain_relevant(lower)
        block = _src.source_block(lower, domain_relevant=_relevant)
        if block:
            base += block
    except Exception:
        pass
    return base


# --------------------------------------------------------------------------- #
#  Module-level default engine + lazy auto-loading of discipline rule-sets     #
# --------------------------------------------------------------------------- #
DEFAULT_ENGINE = MythEngine()
_RULESETS_LOADED = False


def register(rule: Rule) -> Rule:
    return DEFAULT_ENGINE.register(rule)


def register_many(rules: Iterable[Rule]) -> None:
    DEFAULT_ENGINE.register_many(rules)


def _ensure_rulesets_loaded() -> None:
    """Import the discipline rule-set packages once. Each module self-registers
    its rules into ``DEFAULT_ENGINE`` at import time. Done lazily so importing
    ``mythbuster`` stays cheap and a discipline whose imports are heavy only
    loads when the myth-buster is actually used."""
    global _RULESETS_LOADED
    if _RULESETS_LOADED:
        return
    _RULESETS_LOADED = True
    from . import myth_rules  # noqa: F401  (its __init__ imports every ruleset)


def check(text: str, context: Any = None) -> MythResult:
    """Check a claim against the default engine, auto-loading all discipline
    rule-sets on first use. This is the one call the app/CLI needs.

    Registered rules run first (exact arithmetic against the live models). If
    none match, a deterministic general-knowledge reasoner assesses the claim
    against a broad physics/engineering/FSAE knowledge base so the user always
    gets a substantive answer instead of a bare 'no rule matched'. That reasoner
    also handles free-text cross-subsystem claims — "more power doesn't affect
    suspension", "does aero change the brakes" — from an encoded coupling graph,
    naming the physical path. And whenever it can't settle a claim exactly, it
    appends a "where to check / read more" block recommending the tools, texts
    and rulebook an engineer would use. Even a genuinely off-topic claim comes
    back as an honest UNKNOWN that still points somewhere sensible. The whole
    fallback is pure Python — no AI, no network — and never claims more certainty
    than it has (unmatched-but-plausible claims come back as DEPENDS)."""
    _ensure_rulesets_loaded()
    result = DEFAULT_ENGINE.check(text, context)
    if result.verdict != Verdict.UNKNOWN:
        return result
    # No exact rule matched — fall back to the general reasoner.
    try:
        from . import myth_reasoner as _reasoner
        _disc = context.get("_discipline") if isinstance(context, dict) else None
        _r = _reasoner.assess(parse_claim(text), discipline=_disc)
    except Exception:
        _r = None
    if _r is None:
        return result  # keep the honest "couldn't check" message
    try:
        _verdict = Verdict(_r.verdict)
    except ValueError:
        _verdict = Verdict.DEPENDS
    return MythResult(
        verdict=_verdict,
        matched_rule="general_reasoner",
        explanation=_r.explanation,
        discipline=_r.discipline,
        user_values=dict(parse_claim(text).numbers),
        provenance=_r.provenance,
    )


def reference_myths(discipline: Optional[str] = None, context: Any = None,
                    limit: int = 8) -> list[MythResult]:
    _ensure_rulesets_loaded()
    return DEFAULT_ENGINE.reference_myths(discipline, context, limit)


def disciplines() -> list[str]:
    _ensure_rulesets_loaded()
    return DEFAULT_ENGINE.disciplines()


def all_rules(discipline: Optional[str] = None) -> list[Rule]:
    _ensure_rulesets_loaded()
    return DEFAULT_ENGINE.rules(discipline)
