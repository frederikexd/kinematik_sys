"""Tests for the usage-telemetry module (suspension/analytics.py).

Verifies the fire-and-forget contract: events buffer to disk with no Supabase,
the timed() context manager records latency and logs errors-on-raise, identity
is stable per session, and disabling via env is honoured. Headless, no network.
"""
import os
import sys
import json
import time
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import pytest


@pytest.fixture(autouse=True)
def _restore_cwd_after_test():
    """_fresh_module chdir's into the per-test tmp dir; put cwd back afterwards
    so later tests (and their subprocesses) don't inherit a deleted temp dir."""
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


def _fresh_module(tmp_cwd, monkeypatch=None):
    """Import a clean copy of analytics with cwd pointed at a temp dir so the
    local buffer is isolated per test."""
    os.chdir(tmp_cwd)
    import suspension.analytics as ax
    importlib.reload(ax)
    return ax


def _drain(ax, path):
    ax._SINK.flush_blocking(2.0)
    # give the daemon thread a beat to write
    for _ in range(20):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def test_events_buffer_locally_without_supabase(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init(member="Tester", subteam="aero")
    ax.tab_open("kinematics")
    ax.engage("kinematics", "solve")
    ax.complete("kinematics", "solve")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    types = {r["event_type"] for r in rows}
    # Minimal-analytics mode (see _SAMPLE_RATES): only session_start,
    # workflow_complete and error are written; tab_open / feature_engage /
    # first_result are dropped at source because no kept view reads them.
    assert "session_start" in types
    assert "workflow_complete" in types
    assert "tab_open" not in types           # dropped by design (rate 0.0)
    assert "feature_engage" not in types     # dropped by design (rate 0.0)
    assert "first_result" not in types       # dropped by design (rate 0.0)
    assert all(r["member"] == "Tester" for r in rows)


def test_timed_records_latency_and_errors(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init(subteam="suspension")
    with ax.timed("kinematics", "render"):
        time.sleep(0.01)
    raised = False
    try:
        with ax.timed("laptime", "render"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised   # the context manager re-raises
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    renders = [r for r in rows if r["event_type"] == "render"]
    errors = [r for r in rows if r["event_type"] == "error"]
    # Minimal-analytics mode: latency 'render' pings are dropped at source
    # (rate 0.0); the error-on-raise path is metric-critical and always kept.
    assert renders == []
    assert any(r["feature"] == "laptime" and r["error_kind"] == "ValueError"
               for r in errors)


def test_first_result_is_idempotent(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init()
    ax.first_result()
    ax.first_result()
    ax.first_result()
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    # Minimal-analytics mode drops first_result at source (rate 0.0) — the
    # idempotence contract is now simply "never more than one", i.e. zero.
    assert sum(1 for r in rows if r["event_type"] == "first_result") <= 1
    assert sum(1 for r in rows if r["event_type"] == "first_result") == 0


def test_stable_session_id(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init()
    ax.tab_open("a")
    ax.tab_open("b")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    assert len({r["session_id"] for r in rows}) == 1


def test_disable_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KINEMATIK_ANALYTICS", "off")
    ax = _fresh_module(tmp_path)
    ax.init()
    ax.tab_open("kinematics")
    ax.complete("kinematics")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    assert rows == []   # nothing logged when disabled


def test_invalid_event_type_is_ignored(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init()
    ax._emit("not_a_real_type", feature="x")   # should be silently dropped
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    assert all(r["event_type"] != "not_a_real_type" for r in rows)


def test_never_raises_on_bad_payload(tmp_path):
    ax = _fresh_module(tmp_path)
    ax.init()
    # a payload that can't be json-serialised must not raise; default=str saves it
    class Weird:
        pass
    ax.complete("kinematics", "solve", payload={"obj": Weird()})
    # if we got here without an exception, the contract held
    assert True


if __name__ == "__main__":
    import traceback, tempfile, pathlib
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    for n, f in fns:
        d = tempfile.mkdtemp()
        try:
            import inspect
            params = inspect.signature(f).parameters
            kwargs = {}
            if "tmp_path" in params:
                kwargs["tmp_path"] = pathlib.Path(d)
            if "monkeypatch" in params:
                print(f"~ {n} (skipped: needs pytest monkeypatch)")
                continue
            f(**kwargs)
            print("✓", n)
            passed += 1
        except Exception:
            print("✗", n)
            traceback.print_exc()
    print(f"\n{passed} passed (run via pytest for monkeypatch tests)")


def test_return_visit_logs_new_session_start(tmp_path):
    """The reported bug: a returning user must produce a NEW session_start, and
    each visit must get its own session_id. In headless mode the per-session
    store is the process-level _SESS mirror, so we simulate a 'return' by
    clearing it the way a fresh browser session would be."""
    ax = _fresh_module(tmp_path)
    ax.init(member="Aidan", subteam="aero")           # visit 1
    ax.tab_open("kinematics")
    # simulate the browser closing + returning: fresh per-session state
    ax._SESS.started = False
    ax._SESS.session_id = ""
    ax._SESS.first_result_logged = False
    ax.init(member="Aidan", subteam="aero")           # visit 2 (return)
    ax.tab_open("kinematics")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    starts = [r for r in rows if r["event_type"] == "session_start"]
    assert len(starts) == 2, "a returning visit must log a second session_start"
    # two distinct session ids
    assert len({r["session_id"] for r in rows}) == 2


def test_session_start_not_duplicated_within_one_visit(tmp_path):
    """Within a single visit, repeated init() calls (Streamlit reruns) must NOT
    spam session_start."""
    ax = _fresh_module(tmp_path)
    for _ in range(5):
        ax.init(member="Sam", subteam="suspension")   # 5 reruns, one visit
        ax.tab_open("weight")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    starts = [r for r in rows if r["event_type"] == "session_start"]
    assert len(starts) == 1, "one visit must log exactly one session_start"


def test_visitor_id_recorded(tmp_path):
    ax = _fresh_module(tmp_path)
    # visitor_id must land on a KEPT event type. In minimal-analytics mode
    # tab_open is dropped at source, so assert on workflow_complete instead —
    # and set the id BEFORE init() so the session_start anchor carries it too,
    # mirroring the app (the id resolves synchronously before init fires).
    ax.set_visitor_id("browser-abc-123")
    ax.init(subteam="aero")
    ax.complete("kinematics", "solve")
    path = os.path.join(str(tmp_path), "analytics_buffer.jsonl")
    rows = _drain(ax, path)
    start = [r for r in rows if r["event_type"] == "session_start"][0]
    done = [r for r in rows if r["event_type"] == "workflow_complete"][0]
    assert start["visitor_id"] == "browser-abc-123"
    assert done["visitor_id"] == "browser-abc-123"
