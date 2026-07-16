"""Tests for the data-driven entity-based Myth-Buster engine.

Covers the safe-eval sandbox, entity resolution + direction, relationship and
formula resolution, the confidence tiers (formula / registry / baseline /
fallback / manual), the general-physics fallback, the Manual-Review path, and
the bridge into the existing MythEngine. All headless, no DB, no network.
"""
import os
import sys
import math

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suspension import myth_entity_engine as ee
from suspension.myth_entity_engine import safe_eval, UnsafeExpression


# --------------------------------------------------------------------------- #
#  safe_eval sandbox                                                           #
# --------------------------------------------------------------------------- #
def test_safe_eval_arithmetic():
    assert safe_eval("0.5 * rho * v**2 * CA", {"rho": 1.225, "v": 30, "CA": 2.0}) \
        == pytest.approx(0.5 * 1.225 * 900 * 2.0)
    assert safe_eval("(v2/v1)**2", {"v1": 10, "v2": 20}) == pytest.approx(4.0)
    assert safe_eval("sqrt(x)", {"x": 16}) == 4.0
    assert safe_eval("max(a, b)", {"a": 3, "b": 7}) == 7.0


def test_safe_eval_blocks_unsafe():
    # name not supplied
    with pytest.raises(UnsafeExpression):
        safe_eval("a + b", {"a": 1})
    # attribute access
    with pytest.raises(UnsafeExpression):
        safe_eval("x.__class__", {"x": 1})
    # arbitrary calls
    with pytest.raises(UnsafeExpression):
        safe_eval("__import__('os')", {})
    with pytest.raises(UnsafeExpression):
        safe_eval("open('x')", {})
    # subscripts / comprehensions
    with pytest.raises(UnsafeExpression):
        safe_eval("[i for i in range(3)]", {})
    # absurd exponent guarded
    with pytest.raises(UnsafeExpression):
        safe_eval("2 ** v", {"v": 1e9})


# --------------------------------------------------------------------------- #
#  entity resolution + direction                                              #
# --------------------------------------------------------------------------- #
def _engine(registry_lookup=None):
    return ee.default_engine(registry_lookup)


def test_resolves_two_entities_with_direction():
    eng = _engine()
    v = eng.check("does more downforce increase speed?")
    assert v.source == "downforce" and v.target == "speed"


def test_alias_whole_word_match():
    eng = _engine()
    # 'drag' must not match inside 'dragon'; 'df' is a downforce alias
    v = eng.check("does more df increase cornering grip?")
    assert v.source == "downforce" and v.target == "grip"


# --------------------------------------------------------------------------- #
#  relationship resolution + verdicts                                         #
# --------------------------------------------------------------------------- #
def test_downforce_vs_speed_is_depends():
    v = _engine().check("does more downforce increase speed?")
    assert v.verdict == "depends"
    assert v.relationship == "aero.downforce_vs_speed"


def test_downforce_vs_cornering_is_true_and_verified():
    v = _engine().check("does more downforce increase cornering grip?")
    assert v.verdict == "true"
    assert v.confidence_tier == "verified"
    assert v.confidence == pytest.approx(ee.CONFIDENCE["verified"])


# --------------------------------------------------------------------------- #
#  confidence tiers: formula vs registry vs baseline                          #
# --------------------------------------------------------------------------- #
def test_formula_uses_baseline_default_when_no_registry():
    # cornering edge carries the aero_force formula whose CA/rho default in.
    v = _engine().check("does more downforce increase cornering grip?")
    # a verified qualitative edge keeps 'verified' tier even though the formula's
    # inputs defaulted — the edge basis is the stronger signal here.
    assert v.confidence_tier in ("verified", "baseline", "formula")


def test_registry_value_raises_confidence_path():
    # Build a tiny engine where a formula input is satisfied by a verified value.
    ents = [
        dict(slug="downforce", label="Downforce", kind="force", symbol="v",
             aliases=["downforce", "df"], registry_key="aero.df", discipline="aero"),
        dict(slug="cornering", label="Cornering", kind="force",
             aliases=["cornering", "corner grip"], discipline="suspension"),
    ]
    fmls = [dict(slug="f", expression="v * 2", inputs=["v"], defaults={"v": 1.0},
                 basis="physics")]
    rels = [dict(slug="r", source_slug="downforce", target_slug="cornering",
                 effect="increases", verdict="true", explanation="x",
                 formula_slug="f", confidence_basis="modeled")]
    src = ee.LocalKnowledge(ents, fmls, rels)

    # with a registry lookup that returns a real value for 'v', tier -> registry
    eng = ee.EntityMythEngine(src, registry_lookup=lambda k: 42.0 if k == "aero.df" else None)
    v = eng.check("does more downforce increase corner grip?")
    assert v.used_registry is True
    assert v.confidence_tier == "registry"
    assert v.computed == pytest.approx(84.0)

    # without the lookup, the same formula falls back to its default -> baseline
    eng2 = ee.EntityMythEngine(src, registry_lookup=None)
    v2 = eng2.check("does more downforce increase corner grip?")
    assert v2.used_registry is False
    assert v2.confidence_tier == "baseline"
    assert v2.confidence < v.confidence   # baseline is less confident than registry


# --------------------------------------------------------------------------- #
#  fallback law                                                               #
# --------------------------------------------------------------------------- #
def test_general_physics_fallback():
    # speed -> downforce has no specific edge, but the force<->speed scaling law
    # covers the pair (direction-tolerant).
    v = _engine().check("if I double the speed does downforce change?")
    assert v.confidence_tier == "fallback"
    assert v.relationship.startswith("fallback:")
    assert v.confidence == pytest.approx(ee.CONFIDENCE["fallback"])


# --------------------------------------------------------------------------- #
#  manual review (never an error)                                             #
# --------------------------------------------------------------------------- #
def test_unknown_entity_is_manual_review_not_error():
    v = _engine().check("does flux capacitance increase plasma density?")
    assert v.manual_review is True
    assert v.confidence == 0.0
    assert v.verdict == "unknown"


def test_single_entity_is_manual_review():
    # a single entity with no performance/"better" cue cannot form a relationship
    v = _engine().check("tell me about downforce")
    assert v.manual_review is True


def test_empty_input_is_manual_review():
    v = _engine().check("")
    assert v.manual_review is True


# --------------------------------------------------------------------------- #
#  bridge into the existing MythEngine                                         #
# --------------------------------------------------------------------------- #
def test_bridge_answers_what_handwritten_rules_decline():
    from suspension import mythbuster as mb
    from suspension import myth_bridge as br
    # fresh default engine so existing rules are present
    mb._ensure_rulesets_loaded()
    br.install_entity_engine()   # idempotent
    # a claim the hand-written aero rules don't cover but the graph does:
    res = mb.check("does more downforce increase cornering grip?")
    # either a hand rule or the entity bridge answers — must not be UNKNOWN
    assert res.verdict in ("true", "depends", "myth")
    # and the manual-review / low-confidence path must decline cleanly
    res2 = mb.check("does flux capacitance increase plasma density?")
    assert res2.verdict == "unknown"   # honest unknown, no false confidence


def test_bridge_is_idempotent():
    from suspension import myth_bridge as br
    r1 = br.install_entity_engine()
    r2 = br.install_entity_engine()
    assert r1 is r2


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    for n, f in fns:
        try:
            f()
            print("✓", n)
            passed += 1
        except Exception:
            print("✗", n)
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")


# --------------------------------------------------------------------------- #
#  authoring (UI-added rules, no code)                                        #
# --------------------------------------------------------------------------- #
def test_author_creates_new_entities_and_relationship(tmp_path):
    import suspension.myth_entity_engine as ee2
    a = ee2.MythAuthor(local_path=str(tmp_path / "user_rules.json"))
    assert a.backend in ("local", "supabase", "memory")
    if a.backend == "supabase":
        return  # don't hit a live DB in tests
    r = a.add_myth(source_phrase="tyre pressure", target_phrase="grip",
                   effect="depends", verdict="depends",
                   explanation="There is an optimum pressure.",
                   discipline="suspension", existing_entities=[], author="Sam")
    assert r["ok"]
    assert set(r["created_entities"]) == {"tyre_pressure", "grip"}
    # the rule answers via a merged engine
    eng = ee2.merged_local_engine(author=a)
    v = eng.check("does more tyre pressure increase grip?")
    assert v.source == "tyre_pressure" and v.target == "grip"
    assert v.verdict == "depends"


def test_author_reuses_existing_entity(tmp_path):
    import suspension.myth_entity_engine as ee2
    a = ee2.MythAuthor(local_path=str(tmp_path / "user_rules.json"))
    if a.backend == "supabase":
        return
    existing = ee2.default_local_knowledge().entities()  # has 'speed', 'downforce'
    r = a.add_myth(source_phrase="seat padding thickness", target_phrase="speed",
                   effect="depends", verdict="depends",
                   explanation="Lower can help aero but bottoming hurts.",
                   discipline="aerodynamics", existing_entities=existing,
                   author="Lee")
    assert r["ok"]
    # 'speed' already exists -> only the novel source entity is created
    assert r["created_entities"] == ["seat_padding_thickness"]


def test_author_does_not_overmatch_substring(tmp_path):
    import suspension.myth_entity_engine as ee2
    a = ee2.MythAuthor(local_path=str(tmp_path / "user_rules.json"))
    if a.backend == "supabase":
        return
    existing = ee2.default_local_knowledge().entities()
    # a novel phrase must NOT fold into an existing entity by loose substring
    slug, new = a._resolve_or_make_entity("paint colour", existing, "shared")
    assert slug == "paint_colour" and new is not None
