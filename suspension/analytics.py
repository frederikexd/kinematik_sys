# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
analytics.py — lightweight, fire-and-forget usage telemetry
===========================================================

Captures the interactions that, months later, become the board slide: foot
traffic, individual use, render/pull latency, error rate, retention,
time-to-first-result, the adoption funnel, and the headline hours-saved -> $$.

CONTRACT (non-negotiable)
-------------------------
  * NEVER blocks the UI.   Events are queued and flushed on a background thread;
    an insert that is slow or fails can't stall a render.
  * NEVER crashes the app. Every public call is wrapped so a telemetry bug or a
    dead network degrades to "no data collected", never to an exception in the
    user's face.
  * NEVER collects PII by default. Identity is a random per-session UUID. A
    member name is recorded ONLY if the user types one in (opt-in).
  * Degrades offline. With no Supabase configured (laptop / tests) it buffers to
    a local JSONL file so nothing is lost and the same code path runs.

USAGE (the whole API the app needs)
-----------------------------------
    from suspension import analytics as ax

    ax.init(member=None, subteam="aero")        # once per session (cheap, idempotent)
    ax.tab_open("kinematics")                    # user switched to a tab
    ax.engage("kinematics", "solve")             # user actually ran something
    with ax.timed("kinematics", "render"):       # times a render or data pull
        figure = build_figure(...)
    ax.complete("kinematics", "solve")           # workflow finished -> counts for ROI
    ax.first_result()                            # mark the session's first useful output
    ax.error("kinematics", exc)                  # something failed (reliability)

Everything above is safe to call unconditionally; if telemetry is disabled or
unconfigured the calls are no-ops.
"""

from __future__ import annotations

import os
import json
import time
import queue
import atexit
import random
import threading
import datetime as _dt
import contextlib
from typing import Any, Optional

APP_VERSION = "0.11-cookie-identity"
_LOCAL_BUFFER = os.path.join(os.getcwd(), "analytics_buffer.jsonl")
_TABLE = "analytics_events"

# Write-health tracker — records the outcome of the most recent flush so the
# dashboard can surface silent write failures (the cause of metrics freezing:
# inserts were failing, getting buffered to an ephemeral local file, and never
# replayed). Updated in _Sink._flush.
_LAST_WRITE: dict = {
    "ok": None,       # True/False/None(=no write attempted yet this process)
    "at": None,       # iso timestamp of last attempt
    "error": None,    # last error string, if any
    "sent": 0,        # events successfully sent this process
    "buffered": 0,    # events buffered locally due to failures this process
}

# Controlled event vocabulary — mirrors the CHECK constraint in the schema.
_EVENT_TYPES = {
    "session_start", "tab_open", "feature_engage", "workflow_complete",
    "render", "data_pull", "export", "error", "feature_released",
    "first_result",
}

# --------------------------------------------------------------------------- #
#  Event sampling — write ONLY what the minimal analytics tab actually reads    #
# --------------------------------------------------------------------------- #
#  The lean Analytics tab computes everything from three views:
#      v_roi_summary   → needs 'workflow_complete' events
#      v_retention     → needs any event carrying a session/visitor identity;
#                        'session_start' is the reliable one-per-visit anchor
#      v_error_rate    → needs 'error' events (and the success flag on completes)
#
#  So only THREE event types produce rows any kept view reads: session_start,
#  workflow_complete, error. Every other event type (tab_open, feature_engage,
#  render, data_pull, export, feature_released, first_result) would write rows
#  that nothing queries — pure storage cost. We drop those entirely (rate 0.0)
#  to keep the table as small as possible, which is the explicit goal here.
#
#  To bring a metric back later, re-enable its event type here AND re-create the
#  view it feeds (analytics_schema.sql / analytics_hardening.sql). 1.0 = keep
#  all, 0.0 = drop all, anything between = Bernoulli sample at that rate.
_SAMPLE_RATES: dict = {
    # kept — the only events the minimal tab's three views consume
    "session_start":     1.0,
    "workflow_complete": 1.0,
    "error":             1.0,
    # dropped — no minimal-tab view reads these, so writing them is wasted space
    "tab_open":          0.0,
    "feature_engage":    0.0,
    "render":            0.0,
    "data_pull":         0.0,
    "export":            0.0,
    "feature_released":  0.0,
    "first_result":      0.0,
}


def _keep_event(event_type: str) -> bool:
    """Return True if this event should be recorded, applying per-type sampling.

    In the minimal configuration only session_start, workflow_complete and error
    are written (rate 1.0); every other event type is dropped (rate 0.0) because
    no view in the lean Analytics tab reads it, so storing it is pure cost. An
    unknown event type defaults to 1.0 (kept) so new instrumentation isn't
    silently lost — set an explicit rate above to change that. Sampling is
    independent per call (Bernoulli)."""
    rate = _SAMPLE_RATES.get(event_type, 1.0)
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


# --------------------------------------------------------------------------- #
#  Background sink — one daemon thread drains a queue into Supabase / JSONL    #
# --------------------------------------------------------------------------- #
class _Sink:
    """Owns the queue + flush thread. One instance per process."""

    def __init__(self) -> None:
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=10_000)
        self._client = None
        self._client_tried = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- supabase client (lazy; reuses KinematiK's credential resolver) --
    def _get_client(self):
        if self._client_tried:
            return self._client
        self._client_tried = True
        try:
            from .project import _read_credential
            url = _read_credential("SUPABASE_URL")
            key = _read_credential("SUPABASE_KEY")
            if url and key:
                from supabase import create_client
                self._client = create_client(url, key)
        except Exception:
            self._client = None
        return self._client

    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="kinematik-analytics", daemon=True)
            self._thread.start()

    def enqueue(self, event: dict):
        try:
            self._q.put_nowait(event)
        except queue.Full:
            # buffer is full (telemetry far behind) — drop silently rather than
            # block the UI. Losing a few events never matters for these metrics.
            return
        self._ensure_thread()

    # -- the drain loop --
    def _run(self):
        batch: list[dict] = []
        while not self._stop.is_set():
            try:
                ev = self._q.get(timeout=2.0)
                batch.append(ev)
                # opportunistically batch whatever else is waiting
                while len(batch) < 50:
                    try:
                        batch.append(self._q.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                pass
            if batch:
                self._flush(batch)
                batch = []

    def _flush(self, batch: list[dict]):
        client = self._get_client()
        if client is not None:
            try:
                client.table(_TABLE).insert(batch).execute()
                # record success so the dashboard can show write-health
                _LAST_WRITE["ok"] = True
                _LAST_WRITE["at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
                _LAST_WRITE["error"] = None
                _LAST_WRITE["sent"] = _LAST_WRITE.get("sent", 0) + len(batch)
                return
            except Exception as _e:
                # network/db hiccup — record it (so it's not silent) and fall
                # through to the local buffer so the data is not lost; it will
                # be replayed automatically on a later run when the DB is back.
                _LAST_WRITE["ok"] = False
                _LAST_WRITE["at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
                _LAST_WRITE["error"] = str(_e)[:300]
                _LAST_WRITE["buffered"] = _LAST_WRITE.get("buffered", 0) + len(batch)
        self._buffer_local(batch)

    @staticmethod
    def _buffer_local(batch: list[dict]):
        try:
            with open(_LOCAL_BUFFER, "a") as f:
                for ev in batch:
                    f.write(json.dumps(ev, default=str) + "\n")
        except Exception:
            pass  # last resort: drop. Telemetry must never raise.

    def flush_blocking(self, timeout: float = 3.0):
        """Best-effort drain on shutdown."""
        deadline = time.time() + timeout
        while not self._q.empty() and time.time() < deadline:
            time.sleep(0.05)


_SINK = _Sink()
atexit.register(lambda: _SINK.flush_blocking())


# --------------------------------------------------------------------------- #
#  Session state                                                              #
# --------------------------------------------------------------------------- #
#  IMPORTANT: in Streamlit the Python *process* is shared across every browser
#  session and persists across reruns. So per-user / per-visit state CANNOT live
#  in a module-global object — if it did, `session_start` would fire only once
#  for the whole server and a returning user (or a second user) would never be
#  logged. Per-session state therefore lives in st.session_state, which is unique
#  to each browser session and is freshly empty when someone returns. We keep a
#  module-level mirror only as a fallback for non-Streamlit callers (tests,
#  scripts), where "one session per process" is the right behaviour.
class _Session:
    enabled: bool = True
    session_id: str = ""
    member: Optional[str] = None
    subteam: str = "unknown"
    is_new_member: bool = False
    started: bool = False
    first_result_logged: bool = False


_SESS = _Session()   # process-level fallback only (non-Streamlit contexts)

# Process-level set of session_ids that have ALREADY emitted `session_start`.
# Streamlit can execute the script more than once for a single fresh session
# (an immediate widget/component-driven rerun on load), which can slip past the
# per-session_state `started` flag and log the same visit twice — inflating the
# user count. session_id is minted once per browser session and survives
# reruns, so guarding the emit on it here makes session_start idempotent per
# real visit no matter how many times the script re-executes within this server
# process. The set is shared across all sessions in the process, so a genuinely
# new visitor (new session_id) is unaffected and still logs exactly once.
# (Historical note: the old localStorage ?kvid= + location.reload() identity
# scheme created a genuinely SECOND session per visit with a different
# session_id AND visitor_id — which this guard could not dedup. That scheme is
# gone; see suspension/visitor_id.py.)
_STARTED_SESSIONS: set = set()
_STARTED_LOCK = threading.Lock()


def _store():
    """Return the per-session store: st.session_state when running inside a real
    Streamlit script run (unique per browser session), else the process-level
    _SESS mirror. We check for an ACTIVE script-run context, not merely whether
    streamlit imports — otherwise tests and headless scripts (where streamlit is
    installed but there's no session) would get a contextless, non-persistent
    session_state and lose per-session state between calls."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is None:
            return None
        import streamlit as st
        return st.session_state
    except Exception:
        return None


def _sget(key, default=None):
    s = _store()
    if s is not None:
        return s.get(f"_ax_{key}", default)
    return getattr(_SESS, key, default)


def _sset(key, value):
    s = _store()
    if s is not None:
        s[f"_ax_{key}"] = value
    else:
        setattr(_SESS, key, value)


def _opted_out() -> bool:
    """Allow a global kill-switch via env/secret for privacy-conscious teams."""
    try:
        from .project import _read_credential
        val = _read_credential("KINEMATIK_ANALYTICS")
        if val and str(val).lower() in ("0", "off", "false", "no", "disabled"):
            return True
    except Exception:
        pass
    return False


def init(member: Optional[str] = None, subteam: str = "unknown",
         is_new_member: bool = False) -> None:
    """Start (or update) the analytics session. Safe to call every rerun.

    Streamlit reruns the whole script constantly, so this is cheap and emits
    `session_start` exactly ONCE PER BROWSER SESSION — keyed off st.session_state,
    not a process global. That means:
      * the same user's reruns within one visit do NOT double-log;
      * a user who closes the tab and comes back later (a fresh browser session)
        DOES log a new session_start — every logon is recorded;
      * concurrent users each get their own session_start.
    """
    try:
        if _opted_out():
            _sset("enabled", False)
            return
        _sset("enabled", True)
        # stable id per browser session (st.session_state), minted once per visit
        if not _sget("session_id"):
            _sset("session_id", _new_session_id())
        # update mutable identity each call (user may type their name later)
        if member:
            _sset("member", member.strip() or None)
        if subteam:
            _sset("subteam", subteam)
        # emit session_start once per browser session. The visitor_id is
        # resolved SYNCHRONOUSLY on render 1 (suspension/visitor_id.py reads
        # the request cookie / ip+ua fingerprint server-side), so there is no
        # longer any "wait for the id" deferral here — the first emit always
        # carries a durable id.
        if not _sget("started"):
            # Authoritative one-per-visit guard: dedup on the stable session_id in
            # a process-level set. Streamlit can execute the script more than once
            # for a single fresh session (e.g. an immediate widget-driven rerun on
            # load), which could slip past the session_state `started` flag and
            # emit session_start twice — double-counting the visit. Claim the
            # session_id atomically; only the first claimant emits.
            sid = _resolve_session_id()
            with _STARTED_LOCK:
                if sid in _STARTED_SESSIONS:
                    _sset("started", True)
                    return
                _STARTED_SESSIONS.add(sid)
            _sset("started", True)
            _emit("session_start", feature=None, is_new_member=is_new_member)
    except Exception:
        pass


def _new_session_id() -> str:
    import uuid
    return uuid.uuid4().hex


def set_visitor_id(visitor_id: str) -> None:
    """Record a DURABLE per-browser id (persisted by the app to localStorage),
    used to recognise a returning visitor across separate sessions — including
    anonymous ones who never type a name. Safe to call every rerun."""
    try:
        if visitor_id and str(visitor_id).strip():
            _sset("visitor_id", str(visitor_id).strip())
    except Exception:
        pass


def _resolve_session_id() -> str:
    """Return this session's id, minting one if needed. Per browser session in
    Streamlit; per process otherwise."""
    sid = _sget("session_id")
    if not sid:
        sid = _new_session_id()
        _sset("session_id", sid)
    return sid


# --------------------------------------------------------------------------- #
#  Core emit                                                                   #
# --------------------------------------------------------------------------- #
def _emit(event_type: str, *, feature: Optional[str] = None,
          action: Optional[str] = None, duration_ms: Optional[int] = None,
          success: Optional[bool] = None, error_kind: Optional[str] = None,
          value_payload: Optional[dict] = None,
          is_new_member: bool = False) -> None:
    if not _sget("enabled", True):
        return
    if event_type not in _EVENT_TYPES:
        return
    # Sampling gate: drop a fraction of high-frequency latency pings before they
    # ever hit the queue, keeping DB size and egress sustainable on the free tier
    # without affecting any metric-critical event (those default to 100%).
    if not _keep_event(event_type):
        return
    try:
        sid = _resolve_session_id()
        event = {
            "occurred_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "session_id": sid,
            "visitor_id": _sget("visitor_id"),
            "member": _sget("member"),
            "subteam": _sget("subteam", "unknown"),
            "is_new_member": bool(is_new_member or _sget("is_new_member", False)),
            "event_type": event_type,
            "feature": feature,
            "action": action,
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "success": success,
            "error_kind": error_kind,
            "value_payload": value_payload or {},
            "app_version": APP_VERSION,
        }
        _SINK.enqueue(event)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Public verbs                                                                #
# --------------------------------------------------------------------------- #
def tab_open(feature: str) -> None:
    """User switched to / viewed a tab. Top of the adoption funnel."""
    _emit("tab_open", feature=feature)


def engage(feature: str, action: Optional[str] = None) -> None:
    """User actually ran a workflow in a tab (pressed a button, ran a solve).
    Middle of the funnel; counts as individual use."""
    _emit("feature_engage", feature=feature, action=action, success=True)


def complete(feature: str, action: Optional[str] = None,
             payload: Optional[dict] = None) -> None:
    """A workflow produced a useful result. Bottom of the funnel AND the event
    the hours-saved ROI counts. Also marks first_result if none yet."""
    _emit("workflow_complete", feature=feature, action=action, success=True,
          value_payload=payload)
    first_result()


def first_result() -> None:
    """Mark the first useful output of this session (time-to-first-result).
    Idempotent — only the first call per session emits."""
    if _sget("first_result_logged", False):
        return
    _sset("first_result_logged", True)
    _emit("first_result")


def export(feature: str, kind: str) -> None:
    """User exported something (PDF/CSV/file) — a strong value signal."""
    _emit("export", feature=feature, action=kind, success=True)


def has_engaged(feature: str) -> bool:
    """True if engagement has already been recorded for this feature this
    session. Reads the SAME internal store auto_engage writes to, so callers in
    streamlit_app.py don't have to know the key-prefixing convention (a source
    of silent bugs — st.session_state keys and the analytics store differ)."""
    return bool(_sget(f"_ax_engaged_{feature}", False))


def has_completed(feature: str) -> bool:
    """True if a completion has already been recorded for this feature this
    session. Companion to has_engaged()."""
    return bool(_sget(f"_ax_completed_{feature}", False))


def has_opened(feature: str) -> bool:
    """True if a tab_open has already been logged for this feature this
    session. The tab proxy writes this flag directly to st.session_state (not
    via the prefixing store), so read it the same way."""
    try:
        s = _store()
        if s is not None:
            return bool(s.get(f"_ax_open_{feature}", False))
    except Exception:
        pass
    return False


def auto_engage(feature: str, action: Optional[str] = None) -> bool:
    """Fire ``feature_engage`` at most ONCE per session per feature.

    Intended for central/auto instrumentation: safe to call on every rerun
    (e.g. the moment a user first types a number into a tab). Returns True only
    on the call that actually emitted, so callers can chain follow-up logic.
    """
    flag = f"_ax_engaged_{feature}"
    if _sget(flag, False):
        return False
    _sset(flag, True)
    engage(feature, action=action)
    return True


def auto_complete(feature: str, action: Optional[str] = None,
                  payload: Optional[dict] = None,
                  require_engaged: bool = False) -> bool:
    """Fire ``workflow_complete`` at most ONCE per session per feature.

    Intended for central/auto instrumentation: safe to call on every rerun
    (e.g. once a tab has produced a computed result). Also implies engagement,
    so it back-fills ``feature_engage`` if that hasn't fired yet — a completed
    run can never sit below its own engagement in the funnel. Returns True only
    on the call that actually emitted the completion.

    If ``require_engaged`` is True, the completion is only recorded when the
    user has already engaged this feature this session. This is used by central
    auto-instrumentation (e.g. the spinner hook) so that a computation running
    inside a non-visible tab's body — Streamlit executes every tab body on each
    rerun — cannot fabricate a completion for a tab the user never touched.
    """
    if require_engaged and not _sget(f"_ax_engaged_{feature}", False):
        return False
    # a completion logically entails engagement; make sure engage <= complete.
    auto_engage(feature, action=action)
    flag = f"_ax_completed_{feature}"
    if _sget(flag, False):
        return False
    _sset(flag, True)
    complete(feature, action=action, payload=payload)
    return True


def error(feature: str, exc: Any = None, kind: Optional[str] = None) -> None:
    """A feature errored. Drives the reliability (error-rate) metric."""
    ek = kind or (type(exc).__name__ if exc is not None else "error")
    _emit("error", feature=feature, success=False, error_kind=ek)


def render(feature: str, duration_ms: int, action: Optional[str] = None) -> None:
    """Record how long a render took (latency metric)."""
    _emit("render", feature=feature, action=action, duration_ms=duration_ms,
          success=True)


def data_pull(feature: str, duration_ms: int, action: Optional[str] = None) -> None:
    """Record how long a data fetch took (latency metric)."""
    _emit("data_pull", feature=feature, action=action, duration_ms=duration_ms,
          success=True)


@contextlib.contextmanager
def timed(feature: str, kind: str = "render", action: Optional[str] = None):
    """Context manager that times a render or data pull and logs it, and logs an
    `error` if the block raises (then re-raises). One call covers both latency
    and reliability::

        with ax.timed("kinematics", "render"):
            fig = build_figure(...)
    """
    t0 = time.perf_counter()
    try:
        yield
    except Exception as exc:
        error(feature, exc)
        raise
    finally:
        dt_ms = int((time.perf_counter() - t0) * 1000)
        if kind == "data_pull":
            data_pull(feature, dt_ms, action)
        else:
            render(feature, dt_ms, action)


# --------------------------------------------------------------------------- #
#  Replay locally-buffered events (call once when Supabase is reachable again) #
# --------------------------------------------------------------------------- #
def replay_local_buffer() -> int:
    """Push any events buffered to disk (because the DB was down) into Supabase.
    Returns the number replayed. Safe no-op if there's nothing or no client."""
    if not os.path.exists(_LOCAL_BUFFER):
        return 0
    client = _SINK._get_client()
    if client is None:
        return 0
    try:
        with open(_LOCAL_BUFFER) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        if not rows:
            return 0
        for i in range(0, len(rows), 200):
            client.table(_TABLE).insert(rows[i:i + 200]).execute()
        os.remove(_LOCAL_BUFFER)
        return len(rows)
    except Exception:
        return 0


def write_health() -> dict:
    """Outcome of the most recent flush, so the dashboard can show whether
    events are actually reaching Supabase right now. Keys: ok, at, error,
    sent, buffered. ok is None if no write has been attempted this process."""
    return dict(_LAST_WRITE)


def auto_replay_once() -> int:
    """Replay the local buffer at most once per browser session, automatically,
    when Supabase is reachable — so buffered events recover without anyone
    having to click a button (the manual-only path meant the buffer was usually
    wiped by an ephemeral-host restart before it was ever replayed). Returns the
    number replayed (0 if nothing to do / already done this session).

    Honours the kill-switch: with telemetry disabled (KINEMATIK_ANALYTICS=off)
    this is a no-op, so a disabled deploy never flushes a stale local buffer back
    into Supabase (which would otherwise spike writes/egress on the deploy that
    turned analytics off)."""
    try:
        if not _sget("enabled", True):
            return 0
        if _sget("_replayed_once"):
            return 0
        _sset("_replayed_once", True)
        if _SINK._get_client() is None:
            return 0
        return replay_local_buffer()
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
#  Read side — small helpers the dashboard uses to pull the metric views       #
# --------------------------------------------------------------------------- #
_VIEW_ERRORS: dict = {}  # view_name -> last error string (or None if last fetch was clean)


def fetch_view(view_name: str) -> list[dict]:
    """Read a metric view (e.g. 'v_roi_summary') from Supabase. Returns [] if
    unconfigured, empty, or on error. Records whether the LAST fetch errored in
    _VIEW_ERRORS so the dashboard can tell a broken/missing view (e.g. mid-
    migration, when a view is dropped but not yet recreated) apart from a view
    that's simply empty — otherwise both render as blank and look like data
    loss. Use view_error(view_name) to check.

    Honours the kill-switch: when telemetry is disabled (KINEMATIK_ANALYTICS=off)
    this returns [] WITHOUT touching Supabase, so the dashboard's per-rerun view
    pulls generate zero egress. Without this guard, fetch_view kept reading from
    the DB on every Streamlit rerun even with analytics turned off — the read
    side ignored the flag that only ever gated the write side."""
    if not _sget("enabled", True):
        _VIEW_ERRORS[view_name] = None  # disabled, not an error
        return []
    client = _SINK._get_client()
    if client is None:
        _VIEW_ERRORS[view_name] = None  # not an error, just not configured
        return []
    try:
        data = client.table(view_name).select("*").execute().data or []
        _VIEW_ERRORS[view_name] = None
        return data
    except Exception as _e:
        # Most commonly: the view doesn't exist (relation does not exist) because
        # a migration is half-applied, or a permission/RLS error. Record it.
        _VIEW_ERRORS[view_name] = str(_e)[:300]
        return []


def view_error(view_name: str) -> str | None:
    """Returns the error string from the last fetch_view(view_name) call, or
    None if it succeeded (even if it returned zero rows). Lets the UI show
    'view unavailable — re-run migration' instead of a silently-blank tile."""
    return _VIEW_ERRORS.get(view_name)


def is_live() -> bool:
    """True if a Supabase client is configured and telemetry is on."""
    return _sget("enabled", True) and _SINK._get_client() is not None
