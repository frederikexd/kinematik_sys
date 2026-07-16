# ============================================================================
#  KinematiK — sentence-level unit conversion tests (usentence)
# ============================================================================
"""usentence() converts every <number> <metric-unit> pair inside free text so
message strings (errors, captions, history diffs) honour the imperial toggle
without per-site edits. Contract: no-op in metric mode; converts number AND
label together; preserves precision; leaves unitless numbers and unknown
tokens alone; already-converted (US-labelled) strings pass through untouched
so double-conversion is impossible."""
import pytest

import suspension.units as units


@pytest.fixture
def us_mode(monkeypatch):
    monkeypatch.setattr(units, "is_us", lambda: True)


@pytest.fixture
def metric_mode(monkeypatch):
    monkeypatch.setattr(units, "is_us", lambda: False)


def test_metric_mode_is_noop(metric_mode):
    s = "target mass: 230 kg → 228 kg; travel 25 mm; temp 60 °C"
    assert units.usentence(s) == s


def test_converts_number_and_label_together(us_mode):
    out = units.usentence("target mass: 230 kg")
    assert "kg" not in out and "lb" in out
    # 230 kg ≈ 507 lb
    assert "507" in out


def test_preserves_precision(us_mode):
    out = units.usentence("mass 42.00 kg")
    # 42.00 kg -> 92.59 lb, two decimals kept
    assert "92.59 lb" in out


def test_multiple_pairs_in_one_sentence(us_mode):
    out = units.usentence("mass 42.00 kg → 41.00 kg; shifted 1530.0 mm")
    assert "92.59 lb" in out and "90.39 lb" in out
    assert "60.2 in" in out
    assert "kg" not in out and "mm" not in out


def test_temperature_offset_applied(us_mode):
    out = units.usentence("cell peak 60 °C")
    assert "140 °F" in out and "°C" not in out


def test_compound_and_speed_units(us_mode):
    out = units.usentence("rate 28 N/mm at 80 km/h")
    assert "lbf/in" in out and "mph" in out
    assert "N/mm" not in out and "km/h" not in out


def test_unitless_numbers_untouched(us_mode):
    assert units.usentence("bolt count 4, safety factor 1.5") == \
        "bolt count 4, safety factor 1.5"


def test_unknown_tokens_pass_through(us_mode):
    # 'rpm' is not in the conversion table — leave as-is
    assert units.usentence("redline 12000 rpm") == "redline 12000 rpm"


def test_already_us_string_is_stable(us_mode):
    # a string built with the converters (already lb/in) must not convert again
    once = units.usentence("target mass: 230 kg")
    twice = units.usentence(once)
    assert once == twice


def test_signed_numbers_keep_sign(us_mode):
    out = units.usentence("camber gain +2.0 mm per step")
    assert out.startswith("camber gain +") and "in" in out


def test_longest_token_wins(us_mode):
    # "N·m" must convert as torque, not as "N" + stray "m"
    out = units.usentence("torque 12.5 N·m")
    assert "lbf·ft" in out and "12.5 N" not in out


def test_empty_and_none_safe(us_mode):
    assert units.usentence("") == ""
    assert units.usentence(None) is None
