# ============================================================================
#  KinematiK — Integration-ledger persistence tests
# ============================================================================
"""The Integration ledger is the product's single source of truth, yet until
now it lived only in session_state — a Streamlit Cloud process recycle erased
every declaration. These tests pin the new contract: the ledger rides in the
project blob, round-trips through the store, shows up in Project history in
engineering language, and restore reproduces (or clears) it exactly."""
import copy

import pytest

from suspension.history import diff_project, restore
from suspension.interfaces import IntegrationLedger, SubsystemInterface
from suspension.project import ProjectStore, StaleWriteError


def _ledger_dict(wheel_rate=28.0, with_aero=False):
    led = IntegrationLedger(target_mass_kg=230.0)
    led.set(SubsystemInterface(name="suspension", mass_kg=38.0,
                               notes=f"wheel_rate={wheel_rate}"))
    if with_aero:
        led.set(SubsystemInterface(name="aerodynamics", mass_kg=9.0))
    return led.as_dict()


class _MemoryBackend:
    def __init__(self):
        self.blob, self.degraded_reason = {}, None

    def read(self):
        return copy.deepcopy(self.blob)

    def write(self, payload, expected_version=None):
        current = self.blob.get("updated")
        if current is not None and str(current) != str(expected_version):
            raise StaleWriteError(mine=expected_version, theirs=current)
        self.blob = copy.deepcopy(payload)


# --------------------------------------------------------------------------- #
#  Store round-trip
# --------------------------------------------------------------------------- #
def test_ledger_rides_in_payload_and_survives_reload(tmp_path):
    be = _MemoryBackend()
    s = ProjectStore(path=str(tmp_path / "p.json"), backend=be)
    s.ledger = _ledger_dict()
    assert s.save() is True
    assert be.blob["ledger"]["interfaces"]["suspension"]["mass_kg"] == 38.0

    # a fresh session (new store, same backend) gets the declarations back
    s2 = ProjectStore(path=str(tmp_path / "p2.json"), backend=be)
    s2.load()
    assert s2.ledger["interfaces"]["suspension"]["mass_kg"] == 38.0
    # and it parses back into the real object the app uses
    led = IntegrationLedger.from_dict(s2.ledger)
    assert led.get("suspension").mass_kg == 38.0


def test_empty_ledger_key_is_harmless(tmp_path):
    s = ProjectStore(path=str(tmp_path / "p.json"), backend=_MemoryBackend())
    assert s.save() is True            # payload carries {"ledger": {}}
    s.load()
    assert s.ledger == {}


# --------------------------------------------------------------------------- #
#  History speaks ledger
# --------------------------------------------------------------------------- #
def _blob(led=None):
    return {"team_name": "Elbee", "season": "2026", "target_mass_kg": 230.0,
            "weights": [], "decisions": [], "notes": [], "cad_files": [],
            "ledger": led or {}, "updated": "t0"}


def test_diff_reports_declaration_published_and_withdrawn():
    ch = diff_project(_blob(_ledger_dict()), _blob(_ledger_dict(with_aero=True)))
    assert any(c.kind == "added" and c.area == "ledger"
               and "aerodynamics" in c.summary for c in ch)
    ch2 = diff_project(_blob(_ledger_dict(with_aero=True)), _blob(_ledger_dict()))
    assert any(c.kind == "removed" and "aerodynamics" in c.summary for c in ch2)


def test_diff_reports_changed_field_with_values():
    old = _blob(_ledger_dict())
    new = _blob(_ledger_dict())
    new["ledger"]["interfaces"]["suspension"]["mass_kg"] = 41.0
    ch = diff_project(old, new)
    line = next(c for c in ch if c.area == "ledger")
    assert "suspension" in line.summary
    assert "38.0" in line.summary and "41.0" in line.summary


def test_diff_reports_car_level_budget_moves():
    old, new = _blob(_ledger_dict()), _blob(_ledger_dict())
    new["ledger"]["target_mass_kg"] = 228.0
    ch = diff_project(old, new)
    assert any("car-level budgets" in c.summary and "228" in c.summary
               for c in ch)


def test_identical_ledgers_produce_no_lines():
    assert diff_project(_blob(_ledger_dict()), _blob(_ledger_dict())) == []


# --------------------------------------------------------------------------- #
#  Restore semantics
# --------------------------------------------------------------------------- #
def test_restore_brings_back_and_clears_ledger(tmp_path):
    s = ProjectStore(path=str(tmp_path / "p.json"), backend=_MemoryBackend())
    s.ledger = _ledger_dict(with_aero=True)
    assert s.save()

    # restore a version from BEFORE any declarations existed
    ok, msg = restore(s, _blob(led={}))
    assert ok, msg
    assert s.ledger == {}              # cleared, not merged

    # and restoring a version WITH declarations brings them back
    ok, msg = restore(s, _blob(led=_ledger_dict()))
    assert ok, msg
    assert s.ledger["interfaces"]["suspension"]["mass_kg"] == 38.0
