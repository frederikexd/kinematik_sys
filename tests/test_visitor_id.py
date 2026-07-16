"""Regression tests for suspension/visitor_id.py — the reload-free rewrite.

The old scheme resolved the id in the browser and carried it back via
``?kvid=`` + ``window.location.reload()``. The reload booted a SECOND
Streamlit session on every fresh visit (the app visibly loaded twice) and
analytics logged two session_starts under two different visitor_ids —
permanently double-counting every new user. These tests pin the new
contract: the id resolves server-side in ONE call, is sticky for the
session, and the persistence JS never reloads the page.
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suspension import visitor_id as vid_mod
from suspension.visitor_id import resolve_visitor_id


class _FakeContext:
    def __init__(self, cookies=None, ip=None, ua=""):
        self.cookies = cookies or {}
        self.ip_address = ip
        self.headers = {"User-Agent": ua}


class _FakeSt:
    """Minimal stand-in for the streamlit module surface visitor_id touches."""
    def __init__(self, cookies=None, ip=None, ua="", query_params=None):
        self.session_state = {}
        self.context = _FakeContext(cookies=cookies, ip=ip, ua=ua)
        self.query_params = dict(query_params or {})


def test_cookie_wins_and_resolves_on_first_call():
    st = _FakeSt(cookies={"kinematik_vid": "ls-existinguser0000000000"})
    vid, kind = resolve_visitor_id(st)
    assert vid == "ls-existinguser0000000000"
    assert kind == "cookie (durable)"
    # No second render needed, no persistence pass for an already-durable id.
    assert not st.session_state.get("_ax_vid_persisted")


def test_legacy_kvid_query_param_carries_over_and_is_stripped():
    st = _FakeSt(query_params={"kvid": "ls-legacy123"}, ip="1.2.3.4")
    vid, kind = resolve_visitor_id(st)
    assert vid == "ls-legacy123"          # legacy id beats the fingerprint
    assert "kvid" not in st.query_params  # cleaned out of the visible URL


def test_fingerprint_matches_old_recipe():
    import hashlib
    ip, ua = "10.0.0.7", "Mozilla/5.0 (X11; Linux x86_64) " + "x" * 100
    st = _FakeSt(ip=ip, ua=ua)
    vid, kind = resolve_visitor_id(st)
    expected = "fp-" + hashlib.sha256(
        f"{ip}|{ua[:80]}".encode("utf-8")).hexdigest()[:24]
    assert vid == expected                # old fp- ids keep matching
    assert kind == "ip+ua fingerprint"


def test_minted_id_when_nothing_available_and_sticky():
    st = _FakeSt()  # no cookie, no ip, no query param
    vid1, kind1 = resolve_visitor_id(st)
    vid2, kind2 = resolve_visitor_id(st)  # e.g. a widget-driven rerun
    assert vid1.startswith("vid-")
    assert vid1 == vid2                   # identity never changes mid-session
    assert kind1 == "minted uuid"


def test_resolution_is_synchronous_never_none():
    # The old get_durable_visitor_id returned None on render 1 and relied on
    # a reload to settle. The shim must now resolve immediately.
    from suspension.visitor_id import get_durable_visitor_id
    assert get_durable_visitor_id(_FakeSt(ip="9.9.9.9"))


def test_no_page_reload_anywhere_in_persistence_js():
    # Scope to the function that emits browser JS (the module docstring
    # legitimately mentions the retired reload scheme as history).
    src = inspect.getsource(vid_mod._persist_browser_side)
    assert "location.reload" not in src
    assert "location.href" not in src
    assert "replaceState" not in src      # no URL rewriting round-trips either


def test_session_start_emitted_exactly_once_across_reexecutions(tmp_path):
    """Streamlit can execute the script several times for one fresh session;
    session_start must land exactly once (this is the user-count anchor)."""
    import json
    import time
    import importlib
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        import suspension.analytics as ax
        importlib.reload(ax)
        ax.set_visitor_id("fp-samebrowser000000000000")
        for _ in range(3):                # simulate repeated script runs
            ax.init(subteam="aero")
        ax._SINK.flush_blocking(2.0)
        path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
        for _ in range(20):
            if os.path.exists(path):
                break
            time.sleep(0.05)
        time.sleep(0.2)
        with open(path) as f:
            rows = [json.loads(l) for l in f if l.strip()]
        starts = [r for r in rows if r["event_type"] == "session_start"]
        assert len(starts) == 1
        assert starts[0]["visitor_id"] == "fp-samebrowser000000000000"
    finally:
        os.chdir(cwd)
