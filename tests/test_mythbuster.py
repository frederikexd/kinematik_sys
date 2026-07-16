# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
Tests for the cross-discipline myth-buster engine (suspension/mythbuster.py)
and the discipline rule-sets (suspension/myth_rules/).

These lock in three things:
  1. The engine parses, routes and answers deterministically.
  2. The powertrain rules reproduce the original ``check_assumption`` verdicts
     exactly (so migrating to the engine changed no answer).
  3. Every discipline has at least one rule, every rule with a reference claim
     fires on its own example, and no claim ever crashes the engine.
"""
import importlib

import pytest

from suspension import mythbuster as mb


# --------------------------------------------------------------------------- #
#  Parser                                                                      #
# --------------------------------------------------------------------------- #
def test_parser_extracts_common_units():
    c = mb.parse_claim("80 kW at 6000 rpm gives 130 Nm and 110 km/h")
    assert c.num("kw") == 80
    assert c.num("rpm") == 6000
    assert c.num("nm") == 130
    assert c.num("kmh") == 110


def test_parser_handles_thousands_separator_and_decimals():
    c = mb.parse_claim("12,500 rpm and 3.5:1 final drive")
    assert c.num("rpm") == 12500
    assert c.num("ratio") == 3.5


def test_parser_new_units_for_other_disciplines():
    c = mb.parse_claim("400 V pack, 200 A, 80 degC, 350 N, 1.5 g")
    assert c.num("v") == 400
    assert c.num("a") == 200
    assert c.num("degc") == 80
    assert c.num("n") == 350
    assert c.num("g_force") == 1.5


def test_parser_has_keyword_matching():
    c = mb.parse_claim("More DOWNFORCE is always faster")
    assert c.has("downforce")
    assert c.has("faster", "quicker")
    assert not c.has("brake")


# --------------------------------------------------------------------------- #
#  Engine basics                                                               #
# --------------------------------------------------------------------------- #
def test_unknown_claim_returns_unknown_not_crash():
    r = mb.check("the catering for comp is on saturday")
    assert r.verdict == mb.Verdict.UNKNOWN
    assert r.verdict == "unknown"          # str-enum compatibility
    assert r.matched_rule == "no_rule_matched"


def test_result_is_backwards_compatible_with_assumptionresult():
    r = mb.check("twice the load gives twice the grip",
                 {"tire": _default_tire()})
    # old UI/tests rely on these fields:
    assert hasattr(r, "verdict")
    assert hasattr(r, "matched_rule")
    assert hasattr(r, "explanation")
    assert hasattr(r, "user_values")
    assert r.correction == r.explanation   # MythCheck shim


def test_every_verdict_carries_provenance_when_it_fires():
    r = mb.check("a stronger chassis is a stiffer chassis")
    assert r.verdict == mb.Verdict.MYTH
    assert r.provenance                    # non-empty: names what it checked


def test_a_buggy_rule_does_not_take_down_the_engine():
    engine = mb.MythEngine()

    def _boom(claim, ctx):
        raise RuntimeError("rule blew up")

    engine.register(mb.Rule("x.boom", "test", _boom, keywords_any=("boom",)))
    # Should swallow the error and fall through to UNKNOWN, not raise.
    r = engine.check("boom goes the rule")
    assert r.verdict == mb.Verdict.UNKNOWN


def test_duplicate_rule_name_rejected():
    engine = mb.MythEngine()
    rule = mb.Rule("dup", "test", lambda c, x: None, keywords_any=("a",))
    engine.register(rule)
    with pytest.raises(ValueError):
        engine.register(mb.Rule("dup", "test", lambda c, x: None,
                                keywords_any=("b",)))


# --------------------------------------------------------------------------- #
#  Powertrain migration equivalence                                           #
# --------------------------------------------------------------------------- #
def _env():
    from suspension.pt_integration import motor_envelope
    return motor_envelope(peak_torque_nm=130, peak_power_kw=85, redline_rpm=5500)


def _default_tire():
    from suspension import tiremodel
    return tiremodel.default_tire()


POWERTRAIN_CLAIMS = [
    "limiting power to 80 kW caps our rpm at 7000",
    "the redline is exactly 5500 rpm because of the power cap",
    "continuous power is 100 kW",
    "continuous is 50 kW",
    "peak power is at redline",
    "base speed is 2800 rpm",
    "what is base speed",
    "redline gives us 130 km/h",
    "torque at 4000 rpm is 130 Nm",
    "torque at 5000 rpm is 200 Nm",
    "we are within the 80 kW cap",
    "more kw makes us faster",
]


@pytest.mark.parametrize("text", POWERTRAIN_CLAIMS)
def test_engine_matches_original_check_assumption(text):
    """The engine's powertrain verdict must equal the original verdict for every
    claim — proving the migration changed no answer."""
    from suspension.pt_integration import check_assumption
    from suspension.myth_rules.powertrain import PowertrainContext
    env = _env()
    fd = 3.2
    original = check_assumption(text, env, gear_final_drive=fd).verdict
    engine = mb.check(text, PowertrainContext(env=env, gear_final_drive=fd)).verdict
    assert engine == original, f"{text!r}: original={original} engine={engine}"


# --------------------------------------------------------------------------- #
#  Cross-discipline coverage                                                   #
# --------------------------------------------------------------------------- #
def test_all_eight_channels_have_rules():
    # The interface ledger names eight channels; the myth-buster should cover the
    # technical ones. (chassis covers structures; suspension covers tyres+balance.)
    disc = set(mb.disciplines())
    expected = {"powertrain", "suspension", "aerodynamics", "brakes",
                "cooling", "electrics", "chassis"}
    missing = expected - disc
    assert not missing, f"disciplines with no myth rules: {missing}"


def test_reference_claims_fire_on_their_own_rule():
    """Each rule that advertises a reference claim must, when that claim is fed
    back in, route to *itself* — a rule whose example doesn't trigger it is
    mis-keyworded."""
    failures = []
    for rule in mb.all_rules():
        example = getattr(rule.check, "reference_claim", None)
        if not example:
            continue
        r = mb.check(example, _context_for_discipline(rule.discipline))
        if r.matched_rule != rule.name:
            failures.append((rule.name, example, r.matched_rule))
    assert not failures, "reference claims not routing to own rule:\n" + \
        "\n".join(f"  {n}: {ex!r} -> {got}" for n, ex, got in failures)


def test_no_reference_claim_returns_unknown():
    """A registered rule's own example should never come back UNKNOWN — it means
    the rule declined its own claim."""
    unknowns = []
    for rule in mb.all_rules():
        example = getattr(rule.check, "reference_claim", None)
        if not example:
            continue
        r = mb.check(example, _context_for_discipline(rule.discipline))
        if r.verdict == mb.Verdict.UNKNOWN:
            unknowns.append((rule.name, example))
    assert not unknowns, f"rules returning UNKNOWN on own example: {unknowns}"


@pytest.mark.parametrize("text,expected_disc", [
    ("twice the load gives twice the grip", "suspension"),
    ("more negative camber always means more grip", "suspension"),
    ("stiffer springs always make the car faster", "suspension"),
    ("double the speed doubles the downforce", "aerodynamics"),
    ("a bigger brake rotor makes us stop faster", "brakes"),
    ("brake bias should be 50/50", "brakes"),
    ("a bigger radiator always cools better", "cooling"),
    ("all the cells heat up evenly", "cooling"),
    ("a higher voltage pack gives more power", "electrics"),
    ("we can just close the contactor without precharge", "electrics"),
    ("a stronger chassis is a stiffer chassis", "chassis"),
    ("tighter bolts always make a stronger joint", "chassis"),
])
def test_claims_route_to_expected_discipline(text, expected_disc):
    r = mb.check(text, _context_for_discipline(expected_disc))
    assert r.discipline == expected_disc, \
        f"{text!r} routed to {r.discipline} ({r.matched_rule}), expected {expected_disc}"
    assert r.verdict != mb.Verdict.UNKNOWN


def _context_for_discipline(disc):
    if disc == "suspension":
        return {"tire": _default_tire()}
    if disc == "powertrain":
        from suspension.myth_rules.powertrain import PowertrainContext
        return PowertrainContext(env=_env(), gear_final_drive=3.2)
    return None


# --------------------------------------------------------------------------- #
#  Robustness: never crash on adversarial input                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "", "   ", "?????", "1234567890", "kW rpm Nm V A degC",
    "a" * 5000, "\n\t  weird   whitespace  \n",
    "more power more downforce stiffer bigger rotor lighter",  # many keywords
])
def test_engine_never_crashes(text):
    r = mb.check(text)
    assert isinstance(r, mb.MythResult)
    assert r.verdict in (mb.Verdict.MYTH, mb.Verdict.TRUE,
                         mb.Verdict.DEPENDS, mb.Verdict.UNKNOWN)
