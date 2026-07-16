# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""Tests for the throttle return-spring redundancy myth rules (myth_rules/brakes.py).

The point these lock in: the mythbuster must CATCH the "identical springs so the
backup is fine" assumption, and — when the app hands it the live throttle-return
context — refute it against the real numbers, not just recite the rule.
"""
import pytest

from suspension import mythbuster as mb
from suspension.throttle_return import (
    ReturnSpring, ReturnResistance, check_return_redundancy)


def _spring(name, closed, open_):
    return ReturnSpring(name=name, torque_closed_Nm=closed, torque_open_Nm=open_)


# --------------------------------------------------------------------------- #
#  Physics-only path (no live context): must not rubber-stamp the assumption
# --------------------------------------------------------------------------- #
def test_identical_backup_claim_is_not_blindly_true_without_data():
    r = mb.check("the two throttle return springs are identical so if one fails "
                 "the other is fine")
    assert r.matched_rule == "brakes.throttle_identical_backup"
    # Without live numbers it must NOT say TRUE — it says DEPENDS and tells you to
    # check each single-spring-failure case.
    assert r.verdict == "depends"
    assert "single-fault" in r.explanation.lower() or "check" in r.explanation.lower()


def test_sensor_is_not_a_return_spring():
    r = mb.check("the throttle position sensor can count as one of the two "
                 "required return springs")
    assert r.matched_rule == "brakes.throttle_sensor_is_spring"
    assert r.verdict == "myth"


# --------------------------------------------------------------------------- #
#  Live-model path: check the CLAIM against real numbers
# --------------------------------------------------------------------------- #
def test_identical_backup_claim_is_refuted_by_live_fail():
    # Strong primary, weak backup: removing the primary leaves a spring that can't
    # return the throttle -> the assumption is a MYTH, proven by the model.
    springs = [_spring("primary", 5.0, 6.0), _spring("backup", 0.2, 0.3)]
    ctx = {"brakes": {"springs": springs,
                      "resistance": ReturnResistance(friction_Nm=0.4),
                      "margin_target": 1.0}}
    r = mb.check("identical throttle return springs, backup is fine if one unhooks",
                 context=ctx)
    assert r.matched_rule == "brakes.throttle_identical_backup"
    assert r.verdict == "myth"
    assert "without 'primary'" in r.provenance


def test_identical_backup_claim_confirmed_true_when_model_passes():
    springs = [_spring("primary", 2.0, 3.0), _spring("backup", 2.0, 3.0)]
    rr = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.3))
    assert rr.verdict == "PASS"
    r = mb.check("if one throttle spring fails the other still returns it",
                 context={"return_result": rr})
    assert r.matched_rule == "brakes.throttle_identical_backup"
    assert r.verdict == "true"
    # even a TRUE answer reminds you it holds on the real per-spring numbers, not
    # because they're "identical"
    assert "own" in r.explanation.lower() or "each" in r.explanation.lower()


def test_tight_model_makes_the_claim_only_marginal():
    # A return that only just survives the worst single failure -> DEPENDS, not TRUE.
    springs = [_spring("a", 1.15, 1.3), _spring("b", 1.15, 1.3)]
    rr = check_return_redundancy(springs, ReturnResistance(friction_Nm=1.0),
                                 margin_target=0.10, tight_band=1.0)
    assert rr.verdict == "TIGHT"
    r = mb.check("one throttle spring fails, the backup still closes it",
                 context={"return_result": rr})
    assert r.verdict == "depends"


def test_result_object_passed_directly_is_accepted():
    springs = [_spring("primary", 5.0, 6.0), _spring("backup", 0.2, 0.3)]
    rr = check_return_redundancy(springs, ReturnResistance(friction_Nm=0.4))
    # pass the ReturnRedundancyResult straight through as context
    r = mb.check("backup spring identical so one unhooks is fine", context=rr)
    assert r.verdict == "myth"


def test_bad_context_does_not_crash_engine():
    # Garbage context must fall back to the physics answer, never raise.
    for bad in (object(), {"brakes": {"springs": None}}, {"springs": "nope"}, 42):
        r = mb.check("identical throttle return springs so backup is fine",
                     context=bad)
        assert r.verdict in {"myth", "true", "depends", "unknown"}
