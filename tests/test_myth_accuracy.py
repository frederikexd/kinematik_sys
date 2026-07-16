# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
# ============================================================================
"""
test_myth_accuracy.py — accuracy regression tests for the myth-buster reasoner
==============================================================================

These lock in the correctness fixes for claims a lead actually types with no
live context wired up (the common case), where the deterministic general
reasoner answers. The headline bug they guard against: a stated accumulator
draw ABOVE the 80 kW FSAE cap used to read back a green TRUE. It must read MYTH
— that is the whole point of the tool. They also assert the newly-covered
setup topics (camber, toe, rake/ride-height, tyre pressure, dampers, wing size)
resolve to a substantive DEPENDS instead of a bare UNKNOWN.

Pure-Python, deterministic: same claim -> same verdict, every run.
"""
from suspension import mythbuster as mb


def _verdict(text, context=None):
    return mb.check(text, context).verdict.value


# --------------------------------------------------------------------------- #
#  Power-cap: the number must drive the verdict                               #
# --------------------------------------------------------------------------- #
def test_over_cap_draw_is_a_myth_not_true():
    """A car asserting it can DRAW more than 80 kW from the accumulator is
    non-compliant — this must be a MYTH, never a green TRUE."""
    for claim in [
        "we can run 90 kW from the accumulator",
        "the accumulator draws 85 kW peak",
        "our tractive system pulls 100 kW",
    ]:
        assert _verdict(claim) == "myth", claim


def test_under_cap_draw_is_true():
    for claim in [
        "our pack draws 75 kW peak",
        "the accumulator draw is 80 kW",
    ]:
        assert _verdict(claim) == "true", claim


def test_rating_with_limiter_is_depends_not_myth():
    """When the user explicitly limits/clamps the draw or calls the number a
    rating, they already understand the cap — that's the rating-vs-draw nuance
    (DEPENDS), not a compliance myth."""
    for claim in [
        "the motor is rated at 100 kW but we limit accumulator draw",
        "inverter rated 120 kW, draw capped to 80 kW",
    ]:
        assert _verdict(claim) == "depends", claim


def test_over_cap_verdict_names_the_gap():
    """The over-cap explanation must actually cite the number and the cap, so
    it's checkable in a meeting rather than a bare label."""
    res = mb.check("we can run 90 kW from the accumulator")
    assert res.verdict.value == "myth"
    assert "80" in res.explanation and "90" in res.explanation


# --------------------------------------------------------------------------- #
#  Newly-covered high-frequency setup claims: substantive, not UNKNOWN        #
# --------------------------------------------------------------------------- #
def test_common_setup_claims_resolve():
    """These are claims FSAE teams argue about constantly; each must get a
    reasoned verdict (not a bare 'no rule matched')."""
    for claim in [
        "more camber means more grip",
        "toe out helps turn in",
        "we should run maximum rake",
        "running the tyres at 30 psi is better than 20 psi",
        "softer dampers are always more comfortable and faster",
        "the front wing should be as big as possible",
        "lower ride height is always faster",
    ]:
        assert _verdict(claim) == "depends", claim


# --------------------------------------------------------------------------- #
#  Determinism: the honesty contract                                          #
# --------------------------------------------------------------------------- #
def test_determinism():
    """Same claim + same context always yields the same verdict + explanation."""
    for claim in [
        "we can run 90 kW from the accumulator",
        "more camber means more grip",
        "the front wing should be as big as possible",
    ]:
        a, b = mb.check(claim), mb.check(claim)
        assert a.verdict == b.verdict and a.explanation == b.explanation, claim
