# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_bridge.py — wire the data-driven entity engine into the existing Myth-Buster
=================================================================================

This is the one seam between the new entity engine and the shipped
``mythbuster.MythEngine``. It registers a SINGLE low-priority catch-all rule
that delegates to ``EntityMythEngine``. Effect:

  * Every existing hand-written discipline rule still fires first (they have
    priority <= 100; this catch-all sits at 9000), so nothing regresses.
  * Any claim the hand-written rules decline now gets a second chance through
    the entity graph in Supabase — flexible entity resolution instead of a hard
    UNKNOWN.
  * The result is mapped back into the existing ``MythResult`` shape, with the
    confidence score surfaced in ``provenance`` so the current UI shows it with
    no template change. (The UI can read the richer fields off
    ``result.entity_verdict`` when you choose to.)

Migration path: as leads move a discipline's assumptions into Supabase rows, you
can delete that discipline's Python file in ``myth_rules/`` and the entity engine
covers it — the engine code never changes either way.

Usage (called once, e.g. from streamlit_app or myth_rules/__init__):

    from suspension.myth_bridge import install_entity_engine
    install_entity_engine(registry_lookup=my_lookup)   # idempotent
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .mythbuster import (
    CheckOutcome, ParsedClaim, Rule, Verdict, DEFAULT_ENGINE,
)
from . import myth_entity_engine as _ee


# A confidence floor: below this the bridge returns None (declines) so the claim
# falls through to the engine's normal UNKNOWN message rather than surfacing a
# weak guess as an answer. "Manual review" results are below the floor and so
# decline, which is the intended behaviour (no false confidence).
_CONFIDENCE_FLOOR = 0.40

_VERDICT_MAP = {
    "true": Verdict.TRUE, "myth": Verdict.MYTH,
    "depends": Verdict.DEPENDS, "unknown": Verdict.UNKNOWN,
}

_INSTALLED = False
_ENGINE: Optional[_ee.EntityMythEngine] = None


def _build_engine(registry_lookup: Optional[Callable[[str], Optional[float]]]):
    """Prefer Supabase (live, lead-editable); fall back to the bundled defaults
    PLUS any locally-authored rules so myths a lead adds from the UI work even
    with no DB configured."""
    eng = _ee.supabase_engine(registry_lookup)
    if eng is None:
        eng = _ee.merged_local_engine(registry_lookup)
    return eng


# remember the lookup so a rebuild after authoring keeps the same registry wiring
_REGISTRY_LOOKUP: Optional[Callable[[str], Optional[float]]] = None


def install_entity_engine(
        registry_lookup: Optional[Callable[[str], Optional[float]]] = None,
        engine=None) -> Rule:
    """Register the entity engine as a fallback rule on ``DEFAULT_ENGINE``.

    Idempotent: calling twice updates the registry_lookup/engine in place rather
    than registering a duplicate (the underlying ``register`` would raise on a
    duplicate name otherwise).
    """
    global _INSTALLED, _ENGINE, _REGISTRY_LOOKUP
    if registry_lookup is not None:
        _REGISTRY_LOOKUP = registry_lookup
    _ENGINE = engine or _build_engine(registry_lookup)

    if _INSTALLED:
        return _bridge_rule  # already on the engine; _ENGINE was refreshed above

    DEFAULT_ENGINE.register(_bridge_rule)
    _INSTALLED = True
    return _bridge_rule


def refresh_entity_engine():
    """Re-pull the knowledge graph after a myth is added (Supabase or local).

    For the Supabase backend this re-queries; for the local backend it rebuilds
    the engine so the freshly-written JSON rule is loaded. Either way the new
    myth answers immediately, no restart.
    """
    global _ENGINE
    if _ENGINE is None:
        return
    if hasattr(_ENGINE.source, "refresh"):
        _ENGINE.refresh()
    else:
        # local/merged engine has no live source to re-pull — rebuild it
        _ENGINE = _build_engine(_REGISTRY_LOOKUP)


def author():
    """Return a MythAuthor wired to the same backend the engine uses, for the UI
    to write new rules without touching code or SQL."""
    return _ee.MythAuthor()


def existing_entities():
    """Entities the engine currently knows, so the authoring UI can match a typed
    phrase to one that already exists instead of duplicating it."""
    if _ENGINE is not None:
        try:
            return _ENGINE.source.entities()
        except Exception:
            return []
    return []


def _bridge_check(claim: ParsedClaim, context: Any) -> Optional[CheckOutcome]:
    """The catch-all rule body. Runs the entity engine on the raw claim text and
    maps a confident result back into a ``CheckOutcome``; declines (returns None)
    on a manual-review / low-confidence result so the engine's honest UNKNOWN
    message is shown instead of a weak guess."""
    if _ENGINE is None:
        return None
    ev = _ENGINE.check(claim.text)
    if ev.confidence < _CONFIDENCE_FLOOR or ev.manual_review:
        return None
    if ev.confidence_tier == "fallback":
        # "General physics law, no specific edge" — a plausibility guess, not a
        # checked relationship. Decline so the claim falls through to the
        # general/coupling reasoner, which answers from the encoded coupling
        # graph, names the physical path, and appends the "Where to check"
        # sources block. Surfacing the weak guess here would preempt that
        # stronger, sourced answer (and broke the myth-general contract tests).
        return None

    verdict = _VERDICT_MAP.get(ev.verdict, Verdict.UNKNOWN)
    # surface the confidence + computed value in provenance, which the current UI
    # already displays under the verdict — no UI change required.
    prov_bits = [f"entity-engine · {ev.source}\u2192{ev.target}",
                 f"confidence {ev.confidence:.0%} ({ev.confidence_tier})"]
    if ev.computed is not None:
        prov_bits.append(f"computed {ev.computed:.4g}")
    if ev.used_registry:
        prov_bits.append("used verified registry value")
    if ev.provenance:
        prov_bits.append(ev.provenance)
    outcome = CheckOutcome(
        verdict=verdict, explanation=ev.explanation,
        provenance=" · ".join(prov_bits))
    # stash the rich result so a future UI can read it without re-running.
    outcome.entity_verdict = ev   # type: ignore[attr-defined]
    return outcome


# The single fallback rule. No keywords -> it's a candidate for EVERY claim, but
# its high priority number means it only runs after all real rules decline.
_bridge_rule = Rule(
    name="entity_engine.fallback",
    discipline="shared",
    check=_bridge_check,
    keywords_any=(),          # matches everything (engine filters by confidence)
    priority=9000,            # last resort
    description="Data-driven entity/relationship engine (Supabase-backed).",
)
