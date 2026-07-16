"""Durable anonymous visitor id — resolved server-side, with NO page reload.

WHY THIS WAS REWRITTEN
----------------------
The previous scheme resolved the id in the BROWSER: a JS snippet read
localStorage, stuffed the id into a ``?kvid=`` query param and then called
``window.location.reload()`` so Python could read it back.

That reload was the source of two production bugs:

  1. **The app loaded twice on every fresh visit.** A full page reload tears
     down the WebSocket and boots a brand-new Streamlit session (fresh
     ``st.session_state``, fresh session_id). Users saw the UI render, blank,
     and render again.

  2. **Analytics double-counted every new user — permanently.** The doomed
     pre-reload session fired its own ``session_start`` (tagged with a
     throwaway ``fp-``/``ses-`` id once the wait-N-renders deferral expired or
     the fingerprint tier resolved), and the post-reload session fired a
     second ``session_start`` tagged with the ``ls-`` id. Two events, two
     DISTINCT visitor_ids -> the SQL's distinct-visitor count booked one
     human as two users, forever. The process-level ``_STARTED_SESSIONS``
     dedup in analytics.py could not help because the two sessions genuinely
     had different session_ids.

HOW IT WORKS NOW
----------------
Identity is resolved SERVER-SIDE, synchronously, on the very first render —
no round-trip, no reload, no extra rerun, no "wait for the id" deferral:

  1. **Request cookie** (``st.context.cookies['kinematik_vid']``) — set on a
     previous visit; sent with the WebSocket handshake, so it is available
     on render 1. This is the steady-state path for every returning visitor.
  2. **``?kvid=`` query param** — carry-over from the legacy scheme (old
     bookmarks / URLs that still have it). Honoured so pre-rewrite visitors
     keep their original id, then stripped from the visible URL.
  3. **ip+ua fingerprint** — ``sha256(ip | user_agent[:80])``, the SAME
     recipe (and ``fp-`` prefix) as before, so fingerprints minted by the old
     code still match. Stable across visits for the same device; used on a
     browser's first-ever visit, or when cookies are blocked.
  4. **Minted ``vid-`` uuid** — last resort (no ip available, e.g. local dev).

Whatever tier 2-4 resolves is then PERSISTED browser-side by a tiny
fire-and-forget JS snippet (cookie + localStorage on the parent window) so
the next visit short-circuits at tier 1. The snippet never reloads and never
reports back — the server already knows the id it just handed out, so there
is nothing to wait for.

Legacy migration: if the snippet finds an OLD id already sitting in
localStorage (the ``ls-`` scheme), it persists THAT id to the cookie instead
of the fresh one, so from the next visit onward the returning visitor is
recognised under their original id. The one transition visit is logged under
the fingerprint id — a one-time, per-browser discrepancy, not an ongoing
doubling (and the hardening SQL already stitches identity via ``fp-`` ids).

Id prefixes in the data: ``ls-`` legacy localStorage, ``fp-`` fingerprint,
``vid-`` server-minted, ``ses-`` legacy per-session fallback (retired),
``ck-`` legacy CookieManager (retired).
"""

from __future__ import annotations

import uuid

_QP_KEY = "kvid"           # legacy query param that carried the id to Python
_COOKIE_KEY = "kinematik_vid"  # cookie AND localStorage key in the browser
_COOKIE_MAX_AGE = 63072000     # 2 years, in seconds


def resolve_visitor_id(st) -> tuple[str, str]:
    """Return ``(visitor_id, kind)``, resolved synchronously on this render.

    Never returns an empty id, never triggers a reload, and never needs a
    second render to settle — safe to fire ``session_start`` immediately
    after calling this. Pass the streamlit module in as ``st`` so this stays
    import-light and testable.
    """
    # 0. Already resolved this session? Sticky — identity never changes
    #    mid-session, even if the URL or headers do.
    try:
        _cached = st.session_state.get("_ax_vid")
        if _cached:
            return _cached, st.session_state.get("_ax_vid_kind", "cached")
    except Exception:
        pass

    vid, kind = None, ""

    # 1. Durable cookie, read server-side from the request headers. Available
    #    on the FIRST render (unlike the old CookieManager component, which
    #    populated one rerun later and triggered a rerun of its own).
    try:
        _ck = st.context.cookies.get(_COOKIE_KEY)
        if _ck and str(_ck).strip():
            vid, kind = str(_ck).strip(), "cookie (durable)"
    except Exception:
        pass

    # 2. Legacy ?kvid= carry-over. Keeps pre-rewrite visitors on their
    #    original ls- id. Strip it from the visible URL either way.
    if not vid:
        try:
            _qp = st.query_params.get(_QP_KEY)
            if isinstance(_qp, (list, tuple)):
                _qp = _qp[0] if _qp else None
            if _qp and str(_qp).strip():
                vid, kind = str(_qp).strip(), "query param (legacy carry)"
        except Exception:
            pass
    try:
        if st.query_params.get(_QP_KEY) is not None:
            del st.query_params[_QP_KEY]
    except Exception:
        pass

    # 3. Server-side device fingerprint — identical recipe and prefix to the
    #    old code, so previously-minted fp- ids still match. Not perfect
    #    (shared NAT + identical UA collides; ip/UA change mints a new id),
    #    but stable across visits and needs no browser cooperation.
    if not vid:
        try:
            import hashlib as _hl
            _ip = None
            _ua = ""
            try:
                _ip = st.context.ip_address
            except Exception:
                _ip = None
            try:
                _ua = st.context.headers.get("User-Agent") or ""
            except Exception:
                _ua = ""
            if _ip:
                _fp = _hl.sha256(f"{_ip}|{_ua[:80]}".encode("utf-8")).hexdigest()
                vid, kind = "fp-" + _fp[:24], "ip+ua fingerprint"
        except Exception:
            pass

    # 4. Minted uuid — last resort. Durable for future visits once the JS
    #    below lands it in the cookie; if the browser blocks that too, it
    #    degrades to per-session (the honest floor).
    if not vid:
        vid, kind = "vid-" + uuid.uuid4().hex[:24], "minted uuid"

    try:
        st.session_state["_ax_vid"] = vid
        st.session_state["_ax_vid_kind"] = kind
    except Exception:
        pass

    # Persist browser-side so the NEXT visit resolves at tier 1. Only needed
    # when the id didn't come from the cookie, and only once per session.
    # Fire-and-forget: no reload, no callback, nothing to wait for.
    if kind != "cookie (durable)":
        _persist_browser_side(st, vid)

    return vid, kind


def get_durable_visitor_id(st) -> str | None:
    """Back-compat shim for the old API. Always resolves on the first call."""
    vid, _ = resolve_visitor_id(st)
    return vid


def _persist_browser_side(st, vid: str) -> None:
    """Inject a zero-height JS snippet that writes the id to the parent
    window's cookie + localStorage. If a LEGACY id is already in localStorage,
    that one wins (persisted to the cookie) so returning visitors from the
    old scheme keep their original identity from the next visit onward.
    Never reloads the page. Silently a no-op if storage is blocked."""
    try:
        if st.session_state.get("_ax_vid_persisted"):
            return
        st.session_state["_ax_vid_persisted"] = True
    except Exception:
        pass
    _js = f"""
    <script>
    (function() {{
        try {{
            var w = window.parent || window;
            var KEY = "{_COOKIE_KEY}";
            var id = "{vid}";
            var legacy = null;
            try {{ legacy = w.localStorage.getItem(KEY); }} catch (e) {{}}
            if (legacy && legacy !== id) {{ id = legacy; }}
            try {{ w.localStorage.setItem(KEY, id); }} catch (e) {{}}
            try {{
                w.document.cookie = KEY + "=" + id +
                    "; max-age={_COOKIE_MAX_AGE}; path=/; SameSite=Lax" +
                    (w.location.protocol === "https:" ? "; Secure" : "");
            }} catch (e) {{}}
        }} catch (e) {{ /* storage blocked — id stays per-fingerprint/session */ }}
    }})();
    </script>
    """
    try:
        import streamlit as _st
        # st.components.v1.html was deprecated after 2026-06-01; use st.iframe instead.
        # height=0 keeps the injected JS invisible (no layout space consumed).
        _st.iframe(_js, height=0)
    except Exception:
        pass
