# ============================================================================
#  KinematiK — invite-link onboarding tests
# ============================================================================
"""Self-serve team onboarding: a lead mints one link, teammates join with the
right role. These tests pin the Python layer's trust properties (link roles
capped at member/viewer, errors surfaced not raised into the UI, dead links
can't wedge the sign-in loop) and the token's survival through the sign-in
redirect. SQL-side enforcement (expiry, use caps, no-downgrade, revocation)
lives in workspace_invites.sql and is exercised by the RPC contract here."""
import pytest

from suspension.auth import AuthError, Session, SupabaseAuth, Workspace
from suspension import auth_ui


# --------------------------------------------------------------------------- #
#  Stubs
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, owner, name, params):
        self.owner, self.name, self.params = owner, name, params

    def execute(self):
        return self.owner._dispatch(self.name, self.params)


class _StubClient:
    """Implements only .rpc(); records calls; scripted responses per RPC."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def rpc(self, name, params):
        return _RpcCall(self, name, params)

    def _dispatch(self, name, params):
        self.calls.append((name, params))
        r = self.responses.get(name)
        if isinstance(r, Exception):
            raise r
        return _Resp(r)


def _auth_with(client) -> SupabaseAuth:
    a = SupabaseAuth.__new__(SupabaseAuth)
    a._user_client = lambda session: client
    return a


def _session() -> Session:
    return Session(user_id="u1", email="lead@elbee.edu",
                   access_token="tok", refresh_token="r")


WS = "11111111-2222-3333-4444-555555555555"
TOK = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeSt:
    """Just enough of the streamlit surface for the flow functions."""

    def __init__(self, query=None):
        self.session_state = {}
        self.query_params = dict(query or {})
        self.errors, self.successes, self.infos = [], [], []

    def error(self, msg):
        self.errors.append(str(msg))

    def success(self, msg):
        self.successes.append(str(msg))

    def info(self, msg):
        self.infos.append(str(msg))


# --------------------------------------------------------------------------- #
#  Python API layer
# --------------------------------------------------------------------------- #
def test_create_invite_routes_and_returns_token():
    client = _StubClient({"create_workspace_invite": TOK})
    tok = _auth_with(client).create_invite(_session(), WS, role="member",
                                           ttl_hours=72, max_uses=10)
    assert tok == TOK
    name, params = client.calls[0]
    assert name == "create_workspace_invite"
    assert params == {"ws": WS, "invite_role": "member",
                      "ttl_hours": 72, "uses": 10}


def test_create_invite_refuses_privileged_roles_client_side():
    client = _StubClient()
    for bad in ("owner", "lead", "admin"):
        with pytest.raises(AuthError):
            _auth_with(client).create_invite(_session(), WS, role=bad)
    assert client.calls == []          # refused before any network call


def test_redeem_invite_returns_workspace_and_role():
    client = _StubClient({"redeem_workspace_invite": [
        {"workspace_id": WS, "workspace_name": "Elbee Racing",
         "granted_role": "member"}]})
    ws, role = _auth_with(client).redeem_invite(_session(), f"  {TOK}  ")
    assert ws.id == WS and ws.name == "Elbee Racing" and role == "member"
    assert client.calls[0][1] == {"invite_token": TOK}   # trimmed


def test_redeem_invite_surfaces_postgres_message():
    err = Exception({"message": "this invite link has expired — ask a team "
                                "lead for a new one"})
    client = _StubClient({"redeem_workspace_invite": err})
    with pytest.raises(AuthError, match="expired"):
        _auth_with(client).redeem_invite(_session(), TOK)


def test_list_and_revoke_route_correctly():
    client = _StubClient({"list_workspace_invites": [
        {"token": TOK, "role": "member", "use_count": 3, "max_uses": 30}],
        "revoke_workspace_invite": None})
    a = _auth_with(client)
    live = a.list_invites(_session(), WS)
    assert live[0]["use_count"] == 3
    a.revoke_invite(_session(), TOK)
    assert ("revoke_workspace_invite", {"invite_token": TOK}) in client.calls


# --------------------------------------------------------------------------- #
#  Flow: token survives sign-in; redemption activates the workspace
# --------------------------------------------------------------------------- #
def test_capture_join_token_moves_param_to_session():
    st = FakeSt(query={"join": TOK})
    got = auth_ui.capture_join_token(st)
    assert got == TOK
    assert st.session_state[auth_ui._SS_JOIN] == TOK
    assert "join" not in st.query_params       # consumed: no re-redeem loops


def test_capture_join_token_handles_list_param_and_absence():
    st = FakeSt(query={"join": [TOK]})
    assert auth_ui.capture_join_token(st) == TOK
    st2 = FakeSt()
    assert auth_ui.capture_join_token(st2) is None


def test_redeem_pending_invite_happy_path_sets_active_workspace():
    st = FakeSt()
    st.session_state[auth_ui._SS_JOIN] = TOK
    client = _StubClient({"redeem_workspace_invite": [
        {"workspace_id": WS, "workspace_name": "Elbee Racing",
         "granted_role": "member"}]})
    assert auth_ui.redeem_pending_invite(st, _auth_with(client), _session())
    assert st.session_state["_kx_ws_id"] == WS
    assert auth_ui._SS_JOIN not in st.session_state
    assert any("Elbee Racing" in s for s in st.successes)


def test_dead_link_is_surfaced_once_and_cleared():
    st = FakeSt()
    st.session_state[auth_ui._SS_JOIN] = TOK
    err = Exception({"message": "this invite link is no longer valid"})
    client = _StubClient({"redeem_workspace_invite": err})
    ok = auth_ui.redeem_pending_invite(st, _auth_with(client), _session())
    assert ok is False
    assert any("no longer valid" in e for e in st.errors)
    # token cleared: the sign-in flow can never wedge on a dead link
    assert auth_ui._SS_JOIN not in st.session_state
    assert auth_ui.redeem_pending_invite(st, _auth_with(client), _session()) is False
    assert len(st.errors) == 1                  # not re-raised on later runs


def test_redeem_noop_without_token_or_session():
    st = FakeSt()
    assert auth_ui.redeem_pending_invite(st, None, None) is False
    st.session_state[auth_ui._SS_JOIN] = TOK
    assert auth_ui.redeem_pending_invite(st, None, None) is False
    assert st.session_state[auth_ui._SS_JOIN] == TOK   # kept until sign-in


# --------------------------------------------------------------------------- #
#  URL builder
# --------------------------------------------------------------------------- #
def test_build_join_url_with_and_without_base(monkeypatch):
    assert auth_ui.build_join_url(TOK, base_url="https://kinematik.app/") == \
        f"https://kinematik.app/?join={TOK}"
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    rel = auth_ui.build_join_url(TOK)
    assert rel.endswith(f"?join={TOK}")
