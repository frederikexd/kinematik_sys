# ============================================================================
#  KinematiK — optimistic-locking tests (two editors, no silent clobber)
# ============================================================================
"""The project ledger is one JSONB blob. Before this contract, writes were
unconditional upserts: with two subteam leads editing at once, whoever saved
second erased the other's declarations with no trace. These tests lock in the
new contract on every backend:

  * write(payload, expected_version=X) succeeds only if the stored blob's
    `updated` stamp still equals X — otherwise StaleWriteError.
  * expected_version=None refuses to wipe an existing versioned blob.
  * ProjectStore surfaces a conflict as save_conflict (+ save_error) and
    False, never an exception, and reload_latest() recovers.
"""
import copy

import pytest

from suspension.project import ProjectStore, StaleWriteError
from suspension.workspace import (
    LocalWorkspaceBackend, WorkspaceScopedSupabaseBackend, WorkspaceContext,
    Workspace)


# --------------------------------------------------------------------------- #
#  Stub supabase client — enough of the PostgREST chain for the CAS write     #
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self._store = store          # dict[(ws,id)] -> row dict
        self._op = None
        self._payload = None
        self._filters = {}
        self._select = None

    def select(self, cols):
        self._op, self._select = "select", cols
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload = "upsert", payload
        return self

    def eq(self, col, val):
        self._filters[col] = str(val)
        return self

    def _match(self, key, row):
        ws, pid = key
        f = self._filters
        if "workspace_id" in f and f["workspace_id"] != str(ws):
            return False
        if "id" in f and f["id"] != str(pid):
            return False
        if "data->>updated" in f:
            if str((row.get("data") or {}).get("updated")) != f["data->>updated"]:
                return False
        return True

    def execute(self):
        if self._op == "select":
            out = []
            for key, row in self._store.items():
                if self._match(key, row):
                    if "data->>updated" in (self._select or ""):
                        out.append({"updated": (row.get("data") or {}).get("updated")})
                    else:
                        out.append(copy.deepcopy(row))
            return _Resp(out)
        if self._op == "update":
            out = []
            for key, row in self._store.items():
                if self._match(key, row):
                    row.update(copy.deepcopy(self._payload))
                    out.append(copy.deepcopy(row))
            return _Resp(out)          # zero rows == CAS miss, like PostgREST
        if self._op == "upsert":
            p = self._payload
            key = (p.get("workspace_id", "-"), p["id"])
            self._store[key] = copy.deepcopy(p)
            return _Resp([copy.deepcopy(p)])
        raise AssertionError(f"unexpected op {self._op}")


class _StubClient:
    def __init__(self):
        self.rows = {}
        self.postgrest = self          # .auth(token) is called on this

    def auth(self, token):
        pass

    def table(self, name):
        return _Query(self.rows, name)


WS = "11111111-2222-3333-4444-555555555555"


def _ctx():
    return WorkspaceContext(
        workspace=Workspace(id=WS, name="elbee"),
        user_id="u1", access_token="tok", role="owner")


def _backend(client):
    b = WorkspaceScopedSupabaseBackend.__new__(WorkspaceScopedSupabaseBackend)
    b.client = client
    b.ctx = _ctx()
    b.project_id = "default"
    b.degraded_reason = None
    return b


# --------------------------------------------------------------------------- #
#  Backend-level CAS                                                           #
# --------------------------------------------------------------------------- #
def test_first_write_then_cas_write_succeeds():
    be = _backend(_StubClient())
    be.write({"updated": "v1", "workspace_id": WS}, expected_version=None)
    assert be.read_version() == "v1"
    be.write({"updated": "v2", "workspace_id": WS}, expected_version="v1")
    assert be.read_version() == "v2"


def test_second_editor_conflicts_instead_of_clobbering():
    client = _StubClient()
    a, b = _backend(client), _backend(client)
    a.write({"updated": "v1", "weights": ["A's data"], "workspace_id": WS},
            expected_version=None)
    # both editors loaded v1; A saves v2 first
    a.write({"updated": "v2", "weights": ["A's data", "more"], "workspace_id": WS},
            expected_version="v1")
    # B still thinks the base is v1 — must be refused, not silently applied
    with pytest.raises(StaleWriteError) as ei:
        b.write({"updated": "v2b", "weights": ["B wipes A"], "workspace_id": WS},
                expected_version="v1")
    assert ei.value.theirs == "v2"
    # A's data survived
    assert client.rows[(WS, "default")]["data"]["weights"] == ["A's data", "more"]


def test_no_baseline_refuses_to_wipe_versioned_row():
    client = _StubClient()
    a = _backend(client)
    a.write({"updated": "v1", "workspace_id": WS}, expected_version=None)
    with pytest.raises(StaleWriteError):
        _backend(client).write({"updated": "vX", "workspace_id": WS},
                               expected_version=None)


def test_local_workspace_backend_same_contract(tmp_path):
    be = LocalWorkspaceBackend("teamws", root=str(tmp_path))
    be.write({"updated": "v1", "workspace_id": "teamws"}, expected_version=None)
    be.write({"updated": "v2", "workspace_id": "teamws"}, expected_version="v1")
    with pytest.raises(StaleWriteError):
        be.write({"updated": "v3", "workspace_id": "teamws"}, expected_version="v1")
    assert be.read()["updated"] == "v2"


# --------------------------------------------------------------------------- #
#  Store-level behaviour: conflicts surface, never raise; reload recovers      #
# --------------------------------------------------------------------------- #
class _MemoryBackend:
    """Minimal backend honouring the expected_version contract, shared by two
    ProjectStore instances to simulate two browser sessions."""

    def __init__(self):
        self.blob = {}
        self.degraded_reason = None

    def read(self):
        return copy.deepcopy(self.blob)

    def write(self, payload, expected_version=None):
        current = self.blob.get("updated")
        if current is not None and str(current) != str(expected_version):
            raise StaleWriteError(mine=expected_version, theirs=current)
        self.blob = copy.deepcopy(payload)


def test_projectstore_surfaces_conflict_and_reload_recovers(tmp_path):
    shared = _MemoryBackend()
    alice = ProjectStore(path=str(tmp_path / "a.json"), backend=shared)
    bob = ProjectStore(path=str(tmp_path / "b.json"), backend=shared)

    alice.team_name = "Elbee Racing (Alice)"
    assert alice.save() is True

    # Bob loaded BEFORE Alice's save — his baseline is stale
    bob.team_name = "Elbee Racing (Bob)"
    ok = bob.save()
    assert ok is False
    assert bob.save_conflict            # surfaced, not raised
    assert "teammate" in (bob.save_error or "").lower() \
        or "newer" in (bob.save_error or "").lower()
    # Alice's write is untouched
    assert shared.blob["team_name"] == "Elbee Racing (Alice)"

    # Recovery: reload latest, re-apply, save cleanly
    bob.reload_latest()
    assert bob.save_conflict is None
    assert bob.team_name == "Elbee Racing (Alice)"
    bob.team_name = "Elbee Racing (Bob, rebased)"
    assert bob.save() is True
    assert shared.blob["team_name"] == "Elbee Racing (Bob, rebased)"


def test_projectstore_tolerates_legacy_backend_without_kwarg(tmp_path):
    class Legacy:
        def __init__(self):
            self.blob, self.degraded_reason = {}, None

        def read(self):
            return dict(self.blob)

        def write(self, payload):          # old signature: no expected_version
            self.blob = dict(payload)

    s = ProjectStore(path=str(tmp_path / "x.json"), backend=Legacy())
    s.team_name = "still works"
    assert s.save() is True


def test_consecutive_saves_by_same_store_never_self_conflict(tmp_path):
    shared = _MemoryBackend()
    s = ProjectStore(path=str(tmp_path / "s.json"), backend=shared)
    for i in range(3):
        s.target_mass_kg = 230.0 + i
        assert s.save() is True, s.save_error
