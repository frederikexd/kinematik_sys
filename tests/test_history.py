# ============================================================================
#  KinematiK — project history tests (diff engine, fetch fallbacks, restore)
# ============================================================================
"""The audit trail's one job is to never lie: every modelled change is named,
anything unmodelled is flagged as 'other fields changed' rather than hidden,
and restore goes through the optimistic lock so it can't clobber newer work."""
import copy

import pytest

from suspension.history import (
    diff_project, fetch_history, restore, summarize_changes, Change)
from suspension.project import ProjectStore, StaleWriteError


def _blob(**over):
    base = {
        "team_name": "Elbee Racing", "season": "2026", "target_mass_kg": 230.0,
        "weights": [
            {"team": "electrics", "name": "Accumulator", "mass_g": 42000.0,
             "source": "manual", "material": "", "qty": 1, "note": ""},
            {"team": "suspension", "name": "Front upright", "mass_g": 800.0,
             "source": "cad_estimate", "material": "Al 7075", "qty": 2, "note": ""},
        ],
        "decisions": [
            {"team": "suspension", "title": "Pull-rod front", "date": "2026-06-01",
             "rationale": "packaging", "author": "FT", "tags": "", "part": ""},
        ],
        "notes": [], "cad_files": [], "updated": "2026-07-01T00:00:00Z",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
#  Diff engine
# --------------------------------------------------------------------------- #
def test_identical_blobs_diff_empty():
    assert diff_project(_blob(), _blob()) == []


def test_scalar_change_is_named():
    ch = diff_project(_blob(), _blob(target_mass_kg=228.0))
    assert len(ch) == 1 and ch[0].area == "params"
    assert "230" in ch[0].summary and "228" in ch[0].summary


def test_weight_added_removed_and_mass_changed():
    old, new = _blob(), _blob()
    new["weights"] = copy.deepcopy(old["weights"])
    new["weights"][0]["mass_g"] = 41000.0                      # changed
    del new["weights"][1]                                       # removed
    new["weights"].append({"team": "aero", "name": "Rear wing",
                           "mass_g": 3500.0, "qty": 1})         # added
    kinds = {(c.kind, c.area) for c in diff_project(old, new)}
    assert ("added", "weights") in kinds
    assert ("removed", "weights") in kinds
    assert ("changed", "weights") in kinds
    changed = [c for c in diff_project(old, new) if c.kind == "changed"][0]
    assert "42.00 kg" in changed.summary and "41.00 kg" in changed.summary


def test_same_name_different_team_not_merged():
    old = _blob()
    new = _blob()
    new["weights"].append({"team": "aero", "name": "Front upright",
                           "mass_g": 500.0, "qty": 1})
    ch = diff_project(old, new)
    assert len(ch) == 1 and ch[0].kind == "added"


def test_decision_rationale_edit_is_reported():
    new = _blob()
    new["decisions"][0]["rationale"] = "packaging + lower CoG"
    ch = diff_project(_blob(), new)
    assert any(c.area == "decisions" and c.kind == "changed" for c in ch)


def test_unmodelled_field_never_hides():
    ch = diff_project(_blob(), _blob(some_future_field={"x": 1}))
    assert any(c.area == "other" and "some_future_field" in c.summary for c in ch)


def test_metadata_only_changes_are_ignored():
    assert diff_project(_blob(), _blob(updated="2026-07-02T00:00:00Z",
                                       workspace_id="abc")) == []


def test_summarize_counts_by_area():
    new = _blob(target_mass_kg=228.0)
    new["weights"] = []
    s = summarize_changes(diff_project(_blob(), new))
    assert "weights" in s and "params" in s
    assert summarize_changes([]) .startswith("no modelled changes")


# --------------------------------------------------------------------------- #
#  Fetch fallbacks (never raise to the panel)
# --------------------------------------------------------------------------- #
def test_fetch_history_local_backend_reports_reason():
    class Local:                       # no client/ctx — the local JSON backend
        pass
    snaps, reason = fetch_history(Local())
    assert snaps == [] and "local" in reason.lower()


def test_fetch_history_table_error_reports_reason():
    class Ctx:
        workspace_id = "ws"

    class BoomClient:
        def table(self, name):
            raise RuntimeError("relation does not exist")

    class B:
        client, ctx, project_id = BoomClient(), Ctx(), "default"
    snaps, reason = fetch_history(B())
    assert snaps == [] and "project_history.sql" in reason


# --------------------------------------------------------------------------- #
#  Restore: exact, lock-respecting, drift-tolerant
# --------------------------------------------------------------------------- #
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


def test_restore_reproduces_old_state_including_clears(tmp_path):
    be = _MemoryBackend()
    s = ProjectStore(path=str(tmp_path / "p.json"), backend=be)
    s.team_name = "Elbee v2"
    s.target_mass_kg = 225.0
    assert s.save()

    old_version = _blob()              # has 2 weights, mass target 230
    ok, msg = restore(s, old_version)
    assert ok, msg
    assert s.team_name == "Elbee Racing" and s.target_mass_kg == 230.0
    assert len(s.weights) == 2
    # cleared what the old version didn't have
    assert s.notes == [] and s.cad_files == []
    # persisted with a FRESH stamp, not a byte-replay of the old one
    assert be.blob["updated"] != old_version["updated"]


def test_restore_respects_optimistic_lock(tmp_path):
    shared = _MemoryBackend()
    alice = ProjectStore(path=str(tmp_path / "a.json"), backend=shared)
    bob = ProjectStore(path=str(tmp_path / "b.json"), backend=shared)
    alice.team_name = "Alice's save"
    assert alice.save()
    # Bob's baseline is stale; his restore must be refused, not clobber Alice
    ok, msg = restore(bob, _blob())
    assert not ok
    assert "newer" in msg.lower() or "reload" in msg.lower()
    assert shared.blob["team_name"] == "Alice's save"


def test_restore_tolerates_schema_drift_fields(tmp_path):
    s = ProjectStore(path=str(tmp_path / "p.json"), backend=_MemoryBackend())
    old = _blob()
    old["weights"][0]["legacy_field_removed_in_v9"] = "boo"   # unknown kwarg
    ok, msg = restore(s, old)
    assert ok, msg
    assert s.weights[0].name == "Accumulator"


def test_restore_never_raises_on_garbage():
    class Broken:
        def _apply(self, d):
            raise RuntimeError("nope")
    ok, msg = restore(Broken(), {"weights": "not-a-list"})
    assert ok is False and "Restore failed" in msg


# --------------------------------------------------------------------------- #
#  saved_by audit stamp
# --------------------------------------------------------------------------- #
def test_saved_by_is_metadata_not_a_change():
    assert diff_project(_blob(saved_by="a@team.edu"),
                        _blob(saved_by="b@team.edu")) == []


def test_workspace_write_stamps_saved_by(tmp_path):
    from tests.test_optimistic_locking import _StubClient, _backend, WS
    be = _backend(_StubClient())
    be.ctx = type(be.ctx)(workspace=be.ctx.workspace, user_id="u1",
                          access_token="tok", role="owner",
                          email="lead@elbee.edu")
    be.write({"updated": "v1", "workspace_id": WS}, expected_version=None)
    row = be.client.rows[(WS, "default")]
    assert row["data"]["saved_by"] == "lead@elbee.edu"
