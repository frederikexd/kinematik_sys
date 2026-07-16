# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
test_myth_general.py — the "answer anything, or say where to look" contract
===========================================================================

These lock in the generality upgrade for the sanity-check / myth-buster:

  1. Cross-subsystem "does A affect B / A doesn't affect B" claims are answered
     from the coupling graph, naming the physical path — e.g. the headline
     example "more power doesn't affect suspension" must read MYTH, not a
     vague DEPENDS.
  2. A domain-relevant claim the tool can't settle exactly still gets a
     substantive verdict WITH recommended sources, never a bare miss.
  3. When the tool genuinely can't check or answer (off-topic, or a bare
     question), it says so honestly AND recommends where to look — the
     "recommend sources" half of the requirement.
  4. It's all deterministic pure Python: same claim -> same verdict + text.

No AI, no network — the same properties the rest of the myth-buster guarantees.
"""
from suspension import mythbuster as mb
from suspension import myth_reasoner as reasoner
from suspension import myth_coupling as coupling
from suspension import myth_sources as sources


def _res(text, context=None):
    return mb.check(text, context)


def _verdict(text, context=None):
    return _res(text, context).verdict.value


# --------------------------------------------------------------------------- #
#  1. Cross-subsystem coupling: the headline requirement                      #
# --------------------------------------------------------------------------- #
def test_more_power_does_affect_suspension_is_a_myth():
    """The exact example asked for: 'more power doesn't affect suspension'
    must be busted as a MYTH, because power couples to the suspension through
    longitudinal load transfer and traction."""
    for claim in [
        "more power doesnt affect suspension",
        "more power doesn't affect suspension",
        "increasing motor power has no effect on the suspension geometry",
        "the engine has nothing to do with the suspension",
    ]:
        assert _verdict(claim) == "myth", claim


def test_independence_claims_that_are_true_read_true():
    """Where two subsystems really are first-order independent, an
    'A doesn't affect B' claim is TRUE, not reflexively a myth."""
    assert _verdict("cooling doesnt affect handling") == "true"


def test_positive_coupling_claims_confirmed_true():
    for claim in [
        "downforce affects the brakes",
        "aero affects the suspension",
        "mass affects handling",
    ]:
        assert _verdict(claim) == "true", claim


def test_overstated_positive_coupling_is_a_myth():
    """Asserting a coupling that's actually first-order independent is a myth."""
    assert _verdict("cooling affects handling") == "myth"


def test_coupling_answer_names_the_mechanism():
    """A coupling verdict must explain the PATH, so it's checkable in a meeting
    rather than a bare label."""
    r = _res("more power doesnt affect suspension")
    expl = r.explanation.lower()
    assert "load" in expl or "traction" in expl or "squat" in expl


# --------------------------------------------------------------------------- #
#  2 & 3. Always a next step: sources recommended                             #
# --------------------------------------------------------------------------- #
def test_reasoned_verdicts_recommend_sources():
    """Any answer that comes from the general/coupling reasoner (not an exact
    live-model rule) must append a 'where to check / read more' block."""
    for claim in [
        "more power doesnt affect suspension",
        "more coolant flow reduces bearing wear",
        "a lighter flywheel improves throttle response",
        "downforce affects the brakes",
    ]:
        assert "Where to check" in _res(claim).explanation, claim


def test_unknown_claim_still_recommends_sources():
    """Even when nothing can be checked, the message must point the user
    somewhere — that's the 'recommend sources' requirement."""
    r = _res("the moon landing was faked")
    assert r.verdict.value == "unknown"
    assert r.matched_rule == "no_rule_matched"     # honesty contract preserved
    assert "Where to check" in r.explanation


def test_domain_claim_routes_sources_to_the_right_channel():
    """A brakes claim should recommend brake references; an aero claim aero
    references — the source block lines up with the subject."""
    brake_src = _res("quantum tunneling makes the brakes work better").explanation
    assert "Brake" in brake_src or "brake" in brake_src
    aero_src = _res("the paint job affects downforce").explanation
    assert "Aero" in aero_src or "downforce" in aero_src.lower()


# --------------------------------------------------------------------------- #
#  Questions are not assumptions — they stay UNKNOWN (parity with original)   #
# --------------------------------------------------------------------------- #
def test_bare_questions_are_not_manufactured_into_verdicts():
    """'what is base speed' is a question, not an assumption to bust — it must
    stay UNKNOWN, matching the original powertrain checker."""
    for q in [
        "what is base speed",
        "how do dampers work",
        "which tyre compound should we run",
    ]:
        assert _verdict(q) == "unknown", q


def test_numbered_question_is_treated_as_a_claim():
    """A stated number makes even a question-shaped sentence a claim to check,
    so it gets a real verdict (not UNKNOWN). Whether it's myth vs depends is up
    to the power-cap rule; the point here is that the number is engaged."""
    assert _verdict("is 90 kW from the accumulator ok") in ("myth", "depends", "true")
    # And an assertive over-cap draw is unambiguously a myth:
    assert _verdict("we can draw 90 kW from the accumulator") == "myth"


# --------------------------------------------------------------------------- #
#  4. Determinism — the honesty contract                                      #
# --------------------------------------------------------------------------- #
def test_determinism():
    for claim in [
        "more power doesnt affect suspension",
        "cooling doesnt affect handling",
        "the moon landing was faked",
        "more coolant flow reduces bearing wear",
    ]:
        a, b = _res(claim), _res(claim)
        assert a.verdict == b.verdict and a.explanation == b.explanation, claim


# --------------------------------------------------------------------------- #
#  Module-level sanity: the graph and registry are non-trivial                #
# --------------------------------------------------------------------------- #
def test_coupling_graph_and_sources_populated():
    assert coupling.edge_count() >= 12
    assert len(coupling.subject_ids()) >= 10
    # every discipline the sources module knows must yield at least one source
    for disc in ["suspension", "aerodynamics", "brakes", "powertrain",
                 "chassis", "cooling", "electrics", "tires"]:
        got = sources.sources_for("", discipline=disc)
        assert got, disc


def test_off_domain_sources_are_honest():
    """A non-vehicle claim must NOT be handed a race-car textbook as if it
    answered the question — it gets the honest off-domain pointer."""
    got = sources.sources_for("the moon landing was faked",
                              domain_relevant=False)
    assert got and got[0].kind == "ref"


def test_reasoner_never_crashes_on_adversarial_input():
    for text in ["", "   ", "?????", "1234", "a" * 4000,
                 "affects affects affects", "does x affect y"]:
        r = mb.check(text)
        assert isinstance(r, mb.MythResult)
