# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
myth_entity_engine.py — the data-driven, entity-based Myth-Buster heuristic
===========================================================================

WHY THIS EXISTS
---------------
The original Myth-Buster answered claims with hand-written keyword rules:

    if claim.has("more downforce") and claim.has("faster"): ...

That works but is rigid — every new assumption needs a Python edit, and the
matching is brittle string-membership. This module replaces that core with a
*data-driven entity engine*: discipline leads declare ENTITIES, FORMULAS and
RELATIONSHIPS as rows in Supabase (see ``myth_schema.sql``); the engine resolves
a free-text claim into (source_entity, target_entity), finds the relationship
between them, evaluates any attached formula in a safe sandbox, and returns a
verdict + a transparent CONFIDENCE SCORE.

It is still 100% deterministic and transparent — no LLM, no ``eval``. The
"AI-like" experience comes from flexible entity resolution over a curated graph,
not from a model guessing.

DESIGN
------
Four layers, each independently testable:

  1. KnowledgeSource   — loads entities/formulas/relationships. Two
                         implementations: ``SupabaseKnowledge`` (live DB) and
                         ``LocalKnowledge`` (a dict / JSON fixture for tests and
                         offline dev). The engine depends only on the abstract
                         shape, so swapping the source touches no engine code.

  2. safe_eval         — a stdlib-only AST evaluator. Whitelisted node types and
                         a fixed function table (sqrt, sin, pi, …). Anything else
                         raises ``UnsafeExpression``. No ``eval``/``exec``, no
                         attribute access, no names outside the supplied bindings.

  3. EntityResolver    — finds which declared entities appear in a claim, using
                         the entities' own ``aliases`` (word/phrase match), plus
                         the directional cue ("more X → Y?") so the engine knows
                         which entity is source and which is target.

  4. EntityMythEngine  — ties it together: resolve → look up relationship →
                         bind formula inputs (claim numbers > verified registry
                         value > formula default) → evaluate → score confidence →
                         return ``EntityVerdict``. Falls back to a general law,
                         then to ``MANUAL_REVIEW`` — never an error.

INTEGRATION
-----------
``myth_bridge.py`` wraps this engine as a low-priority ``Rule`` in the existing
``MythEngine``, so the Streamlit UI and all existing hand-written rules keep
working unchanged. A specific hand-written rule still wins; the entity engine
catches everything those don't, which is the migration path: move rules to data
at your own pace, deleting Python as you go.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Reuse the existing parser so number/unit extraction stays identical and the
# two engines never disagree on what a claim "contains".
from .mythbuster import parse_claim, ParsedClaim, Verdict


# =========================================================================== #
#  2. safe_eval — stdlib-only deterministic expression sandbox                 #
# =========================================================================== #
class UnsafeExpression(ValueError):
    """Raised when an expression uses a construct outside the whitelist."""


# Operators we allow. Note: no bitwise, no comparison-chaining beyond these,
# no walrus, no comprehensions, no lambdas, no calls except whitelisted funcs.
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CMP_OPS = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}

# Functions/constants a formula may reference. Pure, deterministic, finite.
_SAFE_FUNCS: dict[str, Callable | float] = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "exp": math.exp, "log": math.log, "log10": math.log10,
    "abs": abs, "min": min, "max": max, "pow": pow, "round": round,
    "radians": math.radians, "degrees": math.degrees, "hypot": math.hypot,
    "pi": math.pi, "e": math.e, "tau": math.tau,
}

# Hard cap on expression size to stop pathological inputs (e.g. 2**2**2**...).
_MAX_NODES = 200
_MAX_POW = 1e6   # reject absurd exponents that could blow up memory/time


def safe_eval(expression: str, variables: dict[str, float]) -> float:
    """Evaluate a math expression deterministically with no access to Python.

    Only arithmetic, comparisons, the functions/constants in ``_SAFE_FUNCS``,
    and the names supplied in ``variables`` are permitted. Anything else (calls
    to other names, attribute access, subscripts, comprehensions, imports …)
    raises ``UnsafeExpression``. This is the safety boundary that lets us store
    formulas as strings in a database the team edits.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"could not parse {expression!r}: {exc}") from exc

    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise UnsafeExpression("expression too large")

    env = dict(_SAFE_FUNCS)
    env.update(variables or {})

    def _ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
            raise UnsafeExpression(f"constant {node.value!r} not allowed")
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise UnsafeExpression(f"unknown name {node.id!r}")
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise UnsafeExpression(f"operator {type(node.op).__name__} not allowed")
            left, right = _ev(node.left), _ev(node.right)
            if op is operator.pow and abs(right) > _MAX_POW:
                raise UnsafeExpression("exponent too large")
            return op(left, right)
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise UnsafeExpression("unary operator not allowed")
            return op(_ev(node.operand))
        if isinstance(node, ast.Compare):
            left = _ev(node.left)
            result = True
            for op_node, comp in zip(node.ops, node.comparators):
                op = _CMP_OPS.get(type(op_node))
                if op is None:
                    raise UnsafeExpression("comparison not allowed")
                right = _ev(comp)
                result = result and op(left, right)
                left = right
            return result
        if isinstance(node, ast.BoolOp):
            vals = [_ev(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(vals)
            if isinstance(node.op, ast.Or):
                return any(vals)
            raise UnsafeExpression("bool op not allowed")
        if isinstance(node, ast.IfExp):           # a if cond else b — handy in formulas
            return _ev(node.body) if _ev(node.test) else _ev(node.orelse)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise UnsafeExpression("only direct function calls allowed")
            fn = env.get(node.func.id)
            if not callable(fn):
                raise UnsafeExpression(f"{node.func.id!r} is not a callable")
            if node.keywords:
                raise UnsafeExpression("keyword args not allowed")
            return fn(*[_ev(a) for a in node.args])
        raise UnsafeExpression(f"{type(node).__name__} not allowed")

    out = _ev(tree)
    if isinstance(out, bool):
        return out
    return float(out)


# =========================================================================== #
#  1. Knowledge sources                                                        #
# =========================================================================== #
@dataclass
class Entity:
    slug: str
    label: str
    aliases: tuple[str, ...]
    discipline: str = "shared"
    symbol: str = ""
    canonical_unit: str = ""
    registry_key: Optional[str] = None
    kind: str = ""           # coarse class for fallback laws (force/speed/...)


@dataclass
class Formula:
    slug: str
    expression: str
    inputs: tuple[str, ...]
    defaults: dict[str, float] = field(default_factory=dict)
    basis: str = "physics"    # physics | empirical | heuristic
    label: str = ""
    output_unit: str = ""


@dataclass
class Relationship:
    slug: str
    source_slug: str
    target_slug: str
    effect: str               # increases | decreases | depends | none
    verdict: str              # true | myth | depends | unknown
    explanation: str
    discipline: str = "shared"
    bidirectional: bool = False
    formula_slug: Optional[str] = None
    provenance: str = ""
    confidence_basis: str = "modeled"   # verified | modeled | judgement
    priority: int = 100


@dataclass
class FallbackLaw:
    slug: str
    source_kind: str
    target_kind: str
    effect: str
    verdict: str
    explanation: str
    formula_slug: Optional[str] = None


class KnowledgeSource:
    """Abstract knowledge store. Implementations return plain dataclasses so the
    engine never sees a DB cursor or a dict-shape mismatch."""

    def entities(self) -> list[Entity]:        raise NotImplementedError
    def formulas(self) -> dict[str, Formula]:  raise NotImplementedError
    def relationships(self) -> list[Relationship]: raise NotImplementedError
    def fallback_laws(self) -> list[FallbackLaw]:  return []


class LocalKnowledge(KnowledgeSource):
    """In-memory knowledge from plain dicts/lists — used for tests, offline dev,
    and as the bundled default so the engine works with no DB configured.

    Accepts the same shapes the SQL tables produce, so a fixture can be exported
    straight from Supabase and dropped in here."""

    def __init__(self, entities, formulas, relationships, fallback_laws=None):
        self._entities = [self._as_entity(e) for e in entities]
        self._formulas = {f.slug if isinstance(f, Formula) else f["slug"]:
                          self._as_formula(f) for f in formulas}
        self._rels = [self._as_rel(r) for r in relationships]
        self._fallbacks = [self._as_fallback(f) for f in (fallback_laws or [])]

    @staticmethod
    def _as_entity(e) -> Entity:
        if isinstance(e, Entity):
            return e
        return Entity(
            slug=e["slug"], label=e.get("label", e["slug"]),
            aliases=tuple(a.lower() for a in e.get("aliases", [])),
            discipline=e.get("discipline", "shared"), symbol=e.get("symbol", ""),
            canonical_unit=e.get("canonical_unit", ""),
            registry_key=e.get("registry_key"), kind=e.get("kind", ""))

    @staticmethod
    def _as_formula(f) -> Formula:
        if isinstance(f, Formula):
            return f
        return Formula(
            slug=f["slug"], expression=f["expression"],
            inputs=tuple(f.get("inputs", [])), defaults=dict(f.get("defaults", {})),
            basis=f.get("basis", "physics"), label=f.get("label", ""),
            output_unit=f.get("output_unit", ""))

    @staticmethod
    def _as_rel(r) -> Relationship:
        if isinstance(r, Relationship):
            return r
        return Relationship(
            slug=r["slug"], source_slug=r["source_slug"], target_slug=r["target_slug"],
            effect=r.get("effect", "depends"), verdict=r.get("verdict", "depends"),
            explanation=r["explanation"], discipline=r.get("discipline", "shared"),
            bidirectional=r.get("bidirectional", False),
            formula_slug=r.get("formula_slug"), provenance=r.get("provenance", ""),
            confidence_basis=r.get("confidence_basis", "modeled"),
            priority=r.get("priority", 100))

    @staticmethod
    def _as_fallback(f) -> FallbackLaw:
        if isinstance(f, FallbackLaw):
            return f
        return FallbackLaw(
            slug=f["slug"], source_kind=f.get("source_kind", ""),
            target_kind=f.get("target_kind", ""), effect=f.get("effect", "depends"),
            verdict=f.get("verdict", "depends"), explanation=f["explanation"],
            formula_slug=f.get("formula_slug"))

    def entities(self):       return list(self._entities)
    def formulas(self):       return dict(self._formulas)
    def relationships(self):  return list(self._rels)
    def fallback_laws(self):  return list(self._fallbacks)


class SupabaseKnowledge(KnowledgeSource):
    """Loads the knowledge graph from the Supabase tables in ``myth_schema.sql``.

    Uses the same credential-resolution as the rest of KinematiK (env vars or
    Streamlit secrets) and the same supabase-py client used by the project store,
    so no new connection config is introduced. Results are cached after the first
    load; call ``refresh()`` after a lead edits rows.
    """

    def __init__(self, client=None, url: str = "", key: str = ""):
        if client is None:
            from supabase import create_client
            client = create_client(url, key)
        self.client = client
        self._cache = None

    def refresh(self):
        self._cache = None

    def _load(self):
        if self._cache is not None:
            return self._cache
        ents = self.client.table("myth_entities").select("*").execute().data or []
        fmls = self.client.table("myth_formulas").select("*").execute().data or []
        # the resolved view hands back slugs + the joined formula in one query
        rels = self.client.table("myth_relationship_resolved").select("*").execute().data or []
        fbs = self.client.table("myth_fallback_laws").select("*").execute().data or []
        # the resolved view names formula columns differently; normalise:
        rel_rows = []
        for r in rels:
            rel_rows.append({
                "slug": r["slug"], "source_slug": r["source_slug"],
                "target_slug": r["target_slug"], "effect": r["effect"],
                "verdict": r["verdict"], "explanation": r["explanation"],
                "discipline": r.get("discipline", "shared"),
                "bidirectional": r.get("bidirectional", False),
                "formula_slug": r.get("formula_slug"),
                "provenance": r.get("provenance", ""),
                "confidence_basis": r.get("confidence_basis", "modeled"),
                "priority": r.get("priority", 100),
            })
        self._cache = LocalKnowledge(ents, fmls, rel_rows, fbs)
        return self._cache

    def entities(self):       return self._load().entities()
    def formulas(self):       return self._load().formulas()
    def relationships(self):  return self._load().relationships()
    def fallback_laws(self):  return self._load().fallback_laws()


# =========================================================================== #
#  3. EntityResolver                                                           #
# =========================================================================== #
# Words that signal "increase" / "decrease" intent and the directional framing
# of a claim. Used to assign source vs target when the claim is a question.
_MORE = ("more", "increase", "increases", "increasing", "higher", "raise",
         "raises", "bigger", "greater", "add", "adding", "boost", "extra")
_LESS = ("less", "decrease", "decreases", "reduce", "reduces", "lower",
         "lowering", "smaller", "drop", "cut", "cutting")
# claim connective words that put the target after them: "X increase SPEED"
_DIRECTION_VERBS = ("increase", "increases", "decrease", "decreases", "boost",
                    "improve", "improves", "raise", "raises", "lower", "lowers",
                    "reduce", "reduces", "hurt", "hurts", "help", "helps",
                    "affect", "affects", "change", "changes", "make", "makes",
                    "give", "gives", "mean", "means", "lead to", "result in")


@dataclass
class EntityHit:
    entity: Entity
    alias: str
    position: int       # character index where the alias was found


class EntityResolver:
    """Find declared entities in free text and decide source vs target."""

    def __init__(self, entities: list[Entity]):
        self.entities = entities
        # Build an alias -> entity map, longest aliases first so "down force"
        # matches before "force". Word-boundary regex per alias for whole-word
        # matching (so "drag" doesn't match inside "dragon").
        pairs = []
        for ent in entities:
            for alias in ent.aliases:
                pairs.append((alias, ent))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        self._alias_pairs = [
            (re.compile(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])',
                        re.IGNORECASE), alias, ent)
            for alias, ent in pairs
        ]

    def find(self, text: str) -> list[EntityHit]:
        """Return entity hits in order of appearance, de-duplicated by entity
        (first mention wins), so overlapping aliases of the same entity collapse
        to one hit."""
        lower = text.lower()
        hits: list[EntityHit] = []
        claimed_spans: list[tuple[int, int]] = []
        seen_entities: set[str] = set()
        for rx, alias, ent in self._alias_pairs:
            for m in rx.finditer(lower):
                span = (m.start(), m.end())
                # skip if this span overlaps an already-claimed (longer) alias
                if any(span[0] < e and span[1] > s for s, e in claimed_spans):
                    continue
                if ent.slug in seen_entities:
                    claimed_spans.append(span)
                    continue
                hits.append(EntityHit(entity=ent, alias=alias, position=m.start()))
                claimed_spans.append(span)
                seen_entities.add(ent.slug)
        hits.sort(key=lambda h: h.position)
        return hits

    def directional_pair(self, text: str, hits: list[EntityHit]):
        """Given >=2 hits, pick (source, target) using claim direction.

        Heuristic, deterministic:
          * The generic 'performance' entity ("better/improves") is a LAST-RESORT
            target: if two or more real (non-performance) entities are present, we
            ignore the performance hit so the real target wins ("higher tyre
            pressure improves GRIP" -> target=grip, not 'performance').
          * Reading order otherwise: first entity = source, second = target.
        Returns (source_entity, target_entity) or None if <2 usable hits.
        """
        real = [h for h in hits if h.entity.slug != "performance"]
        usable = real if len(real) >= 2 else hits
        if len(usable) < 2:
            return None
        a, b = usable[0], usable[1]
        return a.entity, b.entity


# =========================================================================== #
#  4. EntityMythEngine + confidence scoring                                    #
# =========================================================================== #
# Confidence tiers, in descending trust. The score is the headline number; the
# tier is the human-readable label the UI shows. These are deliberately coarse
# and fixed so "why is this 0.9?" always has the same answer.
CONFIDENCE = {
    "formula":  0.95,   # a relationship with an evaluable formula, all inputs real
    "registry": 0.85,   # formula used a VERIFIED registry value for an input
    "verified": 0.90,   # qualitative edge the author marked 'verified'
    "modeled":  0.70,   # qualitative edge from a physical model
    "baseline": 0.55,   # formula fell back to a default for an input
    "fallback": 0.45,   # answered by a general physics law, not a specific edge
    "judgement": 0.40,  # edge backed only by engineering judgement
    "manual":   0.0,    # nothing matched -> Manual Review Required
}


@dataclass
class EntityVerdict:
    """The entity engine's answer. Convertible to the existing ``MythResult`` by
    the bridge, so the UI is unchanged."""
    verdict: str                     # 'true' | 'myth' | 'depends' | 'unknown'
    explanation: str
    confidence: float                # 0.0 .. 1.0
    confidence_tier: str             # 'formula' | 'registry' | 'baseline' | ...
    source: str = ""                 # entity slug
    target: str = ""                 # entity slug
    relationship: str = ""           # relationship slug (or 'fallback'/'none')
    provenance: str = ""
    discipline: str = ""
    computed: Optional[float] = None  # the formula result, if one ran
    used_registry: bool = False
    inputs_used: dict = field(default_factory=dict)

    @property
    def manual_review(self) -> bool:
        return self.confidence_tier == "manual"

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict, "explanation": self.explanation,
            "confidence": round(self.confidence, 3),
            "confidence_tier": self.confidence_tier,
            "source": self.source, "target": self.target,
            "relationship": self.relationship, "provenance": self.provenance,
            "discipline": self.discipline, "computed": self.computed,
            "used_registry": self.used_registry, "inputs_used": self.inputs_used,
        }


class EntityMythEngine:
    """The data-driven engine. Construct once with a ``KnowledgeSource`` (and an
    optional ``registry_lookup`` callable for verified live values), then call
    ``check(text)`` per claim.

    ``registry_lookup(key) -> float | None`` is how the engine pulls a VERIFIED
    number for an entity (e.g. the declared ClA from the Integration ledger or
    the Registry tab). If absent or it returns None, the engine uses the
    formula's baseline default and lowers the confidence accordingly.
    """

    def __init__(self, source: KnowledgeSource,
                 registry_lookup: Optional[Callable[[str], Optional[float]]] = None):
        self.source = source
        self.registry_lookup = registry_lookup
        self._reload()

    def _reload(self):
        self._entities = self.source.entities()
        self._formulas = self.source.formulas()
        self._rels = sorted(self.source.relationships(),
                            key=lambda r: (r.priority, r.slug))
        self._fallbacks = self.source.fallback_laws()
        self._resolver = EntityResolver(self._entities)
        self._ent_by_slug = {e.slug: e for e in self._entities}
        # index relationships by (source, target) for O(1) lookup, honouring
        # bidirectional edges.
        self._rel_index: dict[tuple[str, str], Relationship] = {}
        for r in self._rels:
            self._rel_index.setdefault((r.source_slug, r.target_slug), r)
            if r.bidirectional:
                self._rel_index.setdefault((r.target_slug, r.source_slug), r)

    def refresh(self):
        """Re-pull from the source (after a lead edits rows) and rebuild."""
        if hasattr(self.source, "refresh"):
            self.source.refresh()
        self._reload()

    # -- formula evaluation with provenance ------------------------------- #
    def _bind_and_eval(self, formula: Formula, claim: ParsedClaim,
                       source_ent: Entity, target_ent: Entity):
        """Bind a formula's inputs and evaluate it. Returns
        (value, tier, inputs_used, used_registry) where tier reflects the WEAKEST
        input source used (a single baseline default demotes the whole result).
        Raises nothing the caller can't handle — on failure returns (None, ...)."""
        bindings: dict[str, float] = {}
        used_registry = False
        used_default = False
        # numbers parsed out of the claim, by the unit key the parser assigns.
        # We expose them under several friendly names so a formula can ask for
        # 'v', 'v1', 'v2' etc. and get speed-like numbers in order.
        claim_numbers = list(claim.all_numbers)

        # pools of claim numbers by rough kind, in order of appearance
        speedish = [val for k, val in claim_numbers if k in ("kmh", "mph", "ms")]
        generic = [val for _, val in claim_numbers]

        for name in formula.inputs:
            val = None
            # 1) verified registry value, if this input maps to an entity key
            #    (we try the source then target entity's registry_key when the
            #    input name matches the entity symbol or slug)
            for ent in (source_ent, target_ent):
                if ent and ent.registry_key and self.registry_lookup and \
                        name in (ent.symbol, ent.slug, ent.registry_key.split(".")[-1]):
                    rv = self.registry_lookup(ent.registry_key)
                    if rv is not None:
                        val = float(rv)
                        used_registry = True
                        break
            # 2) a number explicitly present in the claim under this exact name
            if val is None and name in claim.numbers:
                val = claim.numbers[name]
            # 3) ordered speed inputs v1/v2 from speed-like claim numbers
            if val is None and name in ("v1", "v2") and speedish:
                idx = 0 if name == "v1" else 1
                if idx < len(speedish):
                    val = speedish[idx]
            # 4) a single generic 'v'/'x' input from the first claim number
            if val is None and name in ("v", "x") and generic:
                val = generic[0]
            # 5) formula baseline default (demotes confidence)
            if val is None and name in formula.defaults:
                val = float(formula.defaults[name])
                used_default = True
            if val is None:
                # an input we couldn't satisfy at all -> can't evaluate
                return None, "manual", bindings, used_registry
            bindings[name] = val

        try:
            value = safe_eval(formula.expression, bindings)
        except UnsafeExpression:
            return None, "manual", bindings, used_registry

        # tier: weakest-link. registry value present -> 'registry'; a default
        # used -> 'baseline'; otherwise full 'formula'.
        if used_default:
            tier = "baseline"
        elif used_registry:
            tier = "registry"
        else:
            tier = "formula"
        return value, tier, bindings, used_registry

    # -- main entry point -------------------------------------------------- #
    def check(self, text: str) -> EntityVerdict:
        claim = parse_claim(text)
        hits = self._resolver.find(text)

        # No recognised entities at all -> Manual Review (not an error).
        if not hits:
            return EntityVerdict(
                verdict="unknown",
                explanation=("No known quantities were recognised in that claim, "
                             "so the entity engine can't reason about it. Flagged "
                             "for manual review — a discipline lead can add the "
                             "missing entity/relationship as data."),
                confidence=CONFIDENCE["manual"], confidence_tier="manual",
                relationship="none")

        pair = self._resolver.directional_pair(text, hits)
        if pair is None:
            # Exactly one entity recognised. If the claim is a "is X better /
            # worse / always faster" question and we have a generic performance
            # entity, pair against it so the fallback laws can give a real verdict
            # instead of 'unknown'. This is what makes single-quantity claims like
            # "is a stiffer chassis better?" resolve.
            ent = hits[0].entity
            perf = self._ent_by_slug.get("performance")
            low = text.lower()
            try:
                from . import myth_knowledge_base as _kb
                _cues = _kb.PERFORMANCE_CUES
            except Exception:
                _cues = ("better", "worse", "best", "always", "faster", "good")
            if perf is not None and ent.slug != "performance" \
                    and any(c in low for c in _cues):
                rel = self._rel_index.get((ent.slug, "performance"))
                if rel is not None:
                    return self._resolve_relationship(rel, claim, ent, perf)
                fb = self._match_fallback(ent, perf)
                if fb is not None:
                    return self._resolve_fallback(fb, claim, ent, perf)
            return EntityVerdict(
                verdict="unknown",
                explanation=(f"Recognised “{ent.label}” but only one quantity — a "
                             f"relationship needs two. Name what you think it "
                             f"affects (e.g. “does more {ent.label.lower()} "
                             f"increase X?”). Flagged for manual review otherwise."),
                confidence=CONFIDENCE["manual"], confidence_tier="manual",
                source=ent.slug, relationship="none", discipline=ent.discipline)

        source_ent, target_ent = pair
        rel = self._rel_index.get((source_ent.slug, target_ent.slug))

        if rel is not None:
            return self._resolve_relationship(rel, claim, source_ent, target_ent)

        # No specific edge -> try a general physics fallback by entity kind.
        fb = self._match_fallback(source_ent, target_ent)
        if fb is not None:
            return self._resolve_fallback(fb, claim, source_ent, target_ent)

        # Nothing -> Manual Review Required (deterministic, never an error).
        return EntityVerdict(
            verdict="unknown",
            explanation=(f"Recognised “{source_ent.label}” and “{target_ent.label}” "
                         f"but no relationship between them is defined yet, and no "
                         f"general law covers this pair. Flagged: Manual Review "
                         f"Required — a lead can add the edge in Supabase "
                         f"(myth_relationships) and it'll answer next time."),
            confidence=CONFIDENCE["manual"], confidence_tier="manual",
            source=source_ent.slug, target=target_ent.slug, relationship="none",
            discipline=source_ent.discipline)

    def _resolve_relationship(self, rel: Relationship, claim: ParsedClaim,
                              source_ent: Entity, target_ent: Entity) -> EntityVerdict:
        computed = None
        inputs_used: dict = {}
        used_registry = False
        # base tier from the edge's own declared confidence_basis
        tier = {"verified": "verified", "modeled": "modeled",
                "judgement": "judgement"}.get(rel.confidence_basis, "modeled")

        formula = self._formulas.get(rel.formula_slug) if rel.formula_slug else None
        if formula is not None:
            value, ftier, inputs_used, used_registry = self._bind_and_eval(
                formula, claim, source_ent, target_ent)
            if value is not None:
                computed = value
                # the formula tier (formula/registry/baseline) takes over when a
                # number was actually produced — it's stronger evidence than the
                # qualitative basis, unless the basis is the weaker 'judgement'.
                if not (rel.confidence_basis == "judgement" and ftier == "baseline"):
                    tier = ftier

        explanation = self._fill(rel.explanation, computed, inputs_used)
        confidence = CONFIDENCE.get(tier, CONFIDENCE["modeled"])
        return EntityVerdict(
            verdict=rel.verdict, explanation=explanation, confidence=confidence,
            confidence_tier=tier, source=source_ent.slug, target=target_ent.slug,
            relationship=rel.slug, provenance=rel.provenance,
            discipline=rel.discipline, computed=computed,
            used_registry=used_registry, inputs_used=inputs_used)

    def _resolve_fallback(self, fb: FallbackLaw, claim: ParsedClaim,
                          source_ent: Entity, target_ent: Entity) -> EntityVerdict:
        computed = None
        inputs_used: dict = {}
        formula = self._formulas.get(fb.formula_slug) if fb.formula_slug else None
        if formula is not None:
            value, _tier, inputs_used, _ur = self._bind_and_eval(
                formula, claim, source_ent, target_ent)
            computed = value
        explanation = self._fill(fb.explanation, computed, inputs_used)
        return EntityVerdict(
            verdict=fb.verdict, explanation=explanation,
            confidence=CONFIDENCE["fallback"], confidence_tier="fallback",
            source=source_ent.slug, target=target_ent.slug,
            relationship=f"fallback:{fb.slug}",
            provenance="general physics law (no specific edge)",
            discipline=source_ent.discipline, computed=computed,
            inputs_used=inputs_used)

    def _match_fallback(self, source_ent: Entity, target_ent: Entity):
        # Try the claim's direction first, then the reverse — a scaling law like
        # "force <-> speed" applies whichever quantity the user named first.
        for s_ent, t_ent in ((source_ent, target_ent), (target_ent, source_ent)):
            for fb in self._fallbacks:
                if ((not fb.source_kind or fb.source_kind == s_ent.kind) and
                        (not fb.target_kind or fb.target_kind == t_ent.kind)):
                    return fb
        return None

    @staticmethod
    def _fill(template: str, computed: Optional[float], inputs: dict) -> str:
        """Fill {ratio:.0f}-style placeholders in an explanation from the formula
        result and inputs. Missing keys are left as-is rather than raising, so a
        template is never broken by an unevaluated formula."""
        ctx = dict(inputs)
        if computed is not None:
            ctx["result"] = computed
            ctx["ratio"] = computed
        class _Safe(dict):
            def __missing__(self, k): return "{" + k + "}"
        try:
            return template.format_map(_Safe(ctx))
        except (ValueError, KeyError):
            return template


# =========================================================================== #
#  Bundled default knowledge — mirrors the SQL seed so the engine works with   #
#  no DB configured (and gives tests a fixture). Leads override via Supabase.   #
# =========================================================================== #
def default_local_knowledge() -> LocalKnowledge:
    """The bundled FSAE physics graph. Loads the comprehensive knowledge base
    (myth_knowledge_base.py) — ~40 entities, ~45 relationships and broad fallback
    laws spanning aero, tyres, mass, suspension, powertrain, braking and chassis —
    so almost any vehicle-dynamics assumption resolves to true/myth/depends rather
    than 'unknown'. Leads extend it from the UI; this stays the offline default."""
    from . import myth_knowledge_base as kb
    return LocalKnowledge(kb.ENTITIES, kb.FORMULAS, kb.RELATIONSHIPS,
                          kb.FALLBACK_LAWS)


def default_engine(registry_lookup=None) -> EntityMythEngine:
    """An engine backed by the bundled default knowledge — the offline/no-DB
    path and the fixture used in tests."""
    return EntityMythEngine(default_local_knowledge(), registry_lookup)


def supabase_engine(registry_lookup=None) -> Optional[EntityMythEngine]:
    """Build an engine from Supabase if credentials are configured, else None.
    Reuses KinematiK's credential resolver so no new config is introduced."""
    try:
        from .project import _read_credential
        url = _read_credential("SUPABASE_URL")
        key = _read_credential("SUPABASE_KEY")
        if not (url and key):
            return None
        return EntityMythEngine(SupabaseKnowledge(url=url, key=key), registry_lookup)
    except Exception:
        return None


# =========================================================================== #
#  AUTHORING — let a lead add a myth from the UI, no code, no SQL              #
# =========================================================================== #
#  A "myth rule" in the data model is just: two entities + a relationship edge
#  between them. The lead types it in plain language ("does more X increase Y?")
#  plus the verdict and explanation; this layer resolves/creates the entities,
#  writes the relationship, and the engine answers it next time. Three persistence
#  backends, tried in order: Supabase (shared, live), a local JSON file (offline /
#  no-DB, still durable), and finally in-memory (last resort).

import json as _json
import os as _os
import re as _re

_LOCAL_RULES_PATH = "myth_rules_user.json"   # next to project.json


def _slugify(text: str) -> str:
    s = _re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or "x"


def _aliases_from_phrase(phrase: str) -> list[str]:
    """Turn a typed phrase into a small alias set: the whole phrase plus its
    significant words, lower-cased and de-duped."""
    p = (phrase or "").strip().lower()
    out = [p] if p else []
    for w in _re.split(r"[^a-z0-9]+", p):
        if len(w) >= 3 and w not in ("the", "more", "less", "does", "increase",
                                     "decrease", "and", "with", "for"):
            if w not in out:
                out.append(w)
    return out or [p or "x"]


class MythAuthor:
    """Write path for UI-authored myth rules.

    backend: 'supabase' | 'local' | 'memory' (auto-selected). The same
    ``add_myth`` call works regardless, so the UI never branches on backend.
    """

    def __init__(self, client=None, local_path: str = _LOCAL_RULES_PATH):
        self.local_path = local_path
        self.client = client
        self.backend = "memory"
        if client is not None:
            self.backend = "supabase"
        else:
            # try to build a supabase client from configured creds
            try:
                from .project import _read_credential
                url = _read_credential("SUPABASE_URL")
                key = _read_credential("SUPABASE_KEY")
                if url and key:
                    from supabase import create_client
                    self.client = create_client(url, key)
                    self.backend = "supabase"
                else:
                    self.backend = "local"
            except Exception:
                self.backend = "local"

    # -- resolve an existing entity by phrase, or make a new-entity spec ---- #
    def _resolve_or_make_entity(self, phrase: str, existing: list[Entity],
                                discipline: str) -> tuple[str, Optional[dict]]:
        """Return (slug, new_entity_dict_or_None). Matches an existing entity if
        the phrase clearly refers to one; otherwise mints a new entity spec.

        Matching is deliberately conservative: an exact slug/label/alias hit, or
        a whole-phrase alias match. We do NOT do loose substring overlap (that
        wrongly folds "grip" into "grip from aero"); when in doubt we create a
        new entity, which is the safe, non-destructive choice.
        """
        low = (phrase or "").strip().lower()
        # exact matches first
        for e in existing:
            if low == e.slug or low == e.label.lower() or low in e.aliases:
                return e.slug, None
        # whole-word match: every word of the phrase is an alias word, or an
        # alias equals the phrase ignoring a leading qualifier ("more"/"less")
        stripped = _re.sub(r"^(more|less|higher|lower|extra)\s+", "", low)
        for e in existing:
            for a in e.aliases:
                if a == stripped or stripped == a:
                    return e.slug, None
        slug = _slugify(phrase)
        return slug, {
            "slug": slug, "label": phrase.strip().title() or slug,
            "discipline": discipline, "aliases": _aliases_from_phrase(phrase),
        }

    def add_myth(self, *, source_phrase: str, target_phrase: str,
                 effect: str, verdict: str, explanation: str,
                 discipline: str = "shared",
                 existing_entities: Optional[list[Entity]] = None,
                 confidence_basis: str = "judgement",
                 author: str = "") -> dict:
        """Create the entities (if needed) and the relationship. Returns a small
        result dict {ok, backend, relationship_slug, created_entities, error}.

        Plain-language inputs only — the lead never sees a slug or a foreign key.
        """
        existing_entities = existing_entities or []
        src_slug, src_new = self._resolve_or_make_entity(
            source_phrase, existing_entities, discipline)
        tgt_slug, tgt_new = self._resolve_or_make_entity(
            target_phrase, existing_entities, discipline)
        rel_slug = f"{discipline}.{src_slug}_vs_{tgt_slug}"
        rel = {
            "slug": rel_slug, "discipline": discipline,
            "source_slug": src_slug, "target_slug": tgt_slug,
            "effect": effect, "verdict": verdict,
            "explanation": explanation.strip(),
            "provenance": f"added by {author}" if author else "lead-authored",
            "confidence_basis": confidence_basis, "priority": 50,
        }
        created = [e["slug"] for e in (src_new, tgt_new) if e]
        try:
            if self.backend == "supabase":
                self._write_supabase(src_new, tgt_new, rel)
            else:
                self._write_local(src_new, tgt_new, rel)
            return {"ok": True, "backend": self.backend,
                    "relationship_slug": rel_slug, "created_entities": created,
                    "error": None}
        except Exception as exc:
            return {"ok": False, "backend": self.backend,
                    "relationship_slug": rel_slug, "created_entities": created,
                    "error": f"{type(exc).__name__}: {exc}"}

    # -- supabase write (upsert entities, then resolve ids, then the edge) -- #
    def _write_supabase(self, src_new, tgt_new, rel):
        for ent in (src_new, tgt_new):
            if ent:
                self.client.table("myth_entities").upsert(
                    ent, on_conflict="slug").execute()
        # fetch the two entity ids by slug
        ids = {}
        for slug in (rel["source_slug"], rel["target_slug"]):
            r = (self.client.table("myth_entities").select("id")
                 .eq("slug", slug).limit(1).execute().data or [])
            if r:
                ids[slug] = r[0]["id"]
        row = {
            "slug": rel["slug"], "discipline": rel["discipline"],
            "source_entity_id": ids.get(rel["source_slug"]),
            "target_entity_id": ids.get(rel["target_slug"]),
            "effect": rel["effect"], "verdict": rel["verdict"],
            "explanation": rel["explanation"], "provenance": rel["provenance"],
            "confidence_basis": rel["confidence_basis"], "priority": rel["priority"],
        }
        self.client.table("myth_relationships").upsert(
            row, on_conflict="slug").execute()

    # -- local JSON write (durable, offline, mirrors the SQL shapes) -------- #
    def _write_local(self, src_new, tgt_new, rel):
        data = self._read_local()
        by_slug = {e["slug"]: e for e in data["entities"]}
        for ent in (src_new, tgt_new):
            if ent:
                by_slug[ent["slug"]] = ent
        data["entities"] = list(by_slug.values())
        rels = {r["slug"]: r for r in data["relationships"]}
        rels[rel["slug"]] = rel
        data["relationships"] = list(rels.values())
        tmp = self.local_path + ".tmp"
        with open(tmp, "w") as f:
            _json.dump(data, f, indent=2)
        _os.replace(tmp, self.local_path)

    def _read_local(self) -> dict:
        if _os.path.exists(self.local_path):
            try:
                with open(self.local_path) as f:
                    d = _json.load(f)
                d.setdefault("entities", [])
                d.setdefault("relationships", [])
                return d
            except Exception:
                pass
        return {"entities": [], "relationships": []}

    def load_user_rules(self) -> "LocalKnowledge":
        """Return a LocalKnowledge of just the user-authored rules (local backend).
        Merged into the engine's knowledge so locally-added myths answer offline."""
        d = self._read_local()
        return LocalKnowledge(d.get("entities", []), [], d.get("relationships", []))


def merged_local_engine(registry_lookup=None,
                        author: Optional[MythAuthor] = None) -> EntityMythEngine:
    """Engine over the bundled defaults PLUS any locally-authored rules, so myths
    a lead adds from the UI work even with no Supabase. Used as the offline path."""
    base = default_local_knowledge()
    author = author or MythAuthor()
    user = author.load_user_rules()
    merged = LocalKnowledge(
        base.entities() + user.entities(),
        list(base.formulas().values()),
        base.relationships() + user.relationships(),
        base.fallback_laws())
    return EntityMythEngine(merged, registry_lookup)
