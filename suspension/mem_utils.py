# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
mem_utils.py — keep the Streamlit app under the hosting RAM limit
=================================================================

Why this exists
---------------
Streamlit Community Cloud gives an app ~1 GB of RAM. This app is figure-heavy
(dozens of Plotly charts, a 3D full-car view) and uses ``st.tabs()`` for
navigation — and ``st.tabs()`` executes the body of EVERY tab on every rerun,
not just the visible one. So without care, a single interaction can build all
those figures and large arrays at once and push a session toward the ceiling.

Calling ``gc.collect()`` on every rerun (as a naive fix does) helps memory a
little but adds a visible stall to every click, because a full collection walks
the entire heap each time. This module does the same job more cheaply and adds
the pieces that actually move peak RAM:

    * ``maybe_collect()`` — throttled garbage collection: a full collect only
      every Nth rerun (and always after a heavy view), so the memory benefit
      stays but the per-click CPU cost goes away.
    * ``release_figure()`` — explicitly drop a Plotly figure's data once it has
      been handed to Streamlit, so the big coordinate arrays don't linger in
      the session for the rest of the run.
    * ``cap_session_log()`` — bound an ever-growing ``session_state`` list (the
      activity log) so it can't accumulate without limit across a long session.
    * ``drop_state()`` — delete a large one-shot blob from ``session_state``
      (e.g. an exported Excel byte-string) after it's been consumed.
    * ``rss_mb()`` / ``memory_guard()`` — read the process RSS and, if it's
      approaching the limit, clear Streamlit's data cache to pull it back.

Everything here is pure Python + the standard library (``gc``, ``os``,
optionally ``resource``). It imports Streamlit lazily and degrades to a no-op
if a call isn't available, so it never breaks a headless import or a test.

Design contract
---------------
    * **Never raise into the app.** Every public function swallows its own
      errors — a memory helper must not be the thing that crashes a tab.
    * **No new dependencies.** RSS is read from ``/proc`` or ``resource``; there
      is no psutil requirement.
    * **Deterministic and observable.** Thresholds are module constants you can
      read and tune; nothing is hidden.
"""
from __future__ import annotations

import gc
import os
from typing import Any, Optional


# --------------------------------------------------------------------------- #
#  Tunables (read them; change them here if the hosting limit changes)         #
# --------------------------------------------------------------------------- #
# Community Cloud guarantees ~1 GB. Leave headroom: start shedding cache well
# before the hard ceiling so a viewer never sees the "over its resource limits"
# page. These are deliberately conservative.
HARD_LIMIT_MB = 1024          # the platform ceiling we must stay under
SOFT_LIMIT_MB = 750           # begin clearing caches above this RSS
CRITICAL_MB = 900             # aggressive cleanup above this RSS

# Throttle: run a full gc.collect() at most once every this many reruns. A
# Plotly-heavy rerun creates a lot of short-lived objects, but collecting every
# single rerun is wasteful; every ~10 keeps memory flat without the per-click
# stall. Heavy views can force a collect regardless via maybe_collect(force=…).
COLLECT_EVERY_N_RERUNS = 10

# session_state key + cap for the activity log (the one unbounded-growth list).
_ACTIVITY_KEY = "_kinematik_activity_log"
ACTIVITY_MAX_PER_SUBSYSTEM = 60


# --------------------------------------------------------------------------- #
#  Lazy Streamlit handle (so this module imports cleanly headless / in tests)  #
# --------------------------------------------------------------------------- #
def _st() -> Optional[Any]:
    try:
        import streamlit as st  # noqa: WPS433 (intentional lazy import)
        return st
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Process RSS (no psutil needed)                                             #
# --------------------------------------------------------------------------- #
def rss_mb() -> Optional[float]:
    """Resident set size of this process in MB, or None if unavailable.

    Reads ``/proc/self/statm`` on Linux (Community Cloud is Linux) and falls
    back to ``resource.getrusage``. Never raises.
    """
    # /proc is the most accurate on Linux containers.
    try:
        with open("/proc/self/statm", "r") as fh:
            pages = int(fh.read().split()[1])          # resident pages
        page_size = os.sysconf("SC_PAGE_SIZE")          # bytes per page
        return pages * page_size / (1024 * 1024)
    except Exception:
        pass
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports ru_maxrss in KB; macOS in bytes. Assume Linux (KB) but
        # guard the absurd-bytes case.
        mb = ru / 1024.0
        if mb > 1024 * 64:        # >64 GB => it was bytes, convert again
            mb = ru / (1024.0 * 1024.0)
        return mb
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Throttled garbage collection                                               #
# --------------------------------------------------------------------------- #
_RERUN_COUNTER_KEY = "_mem_rerun_counter"


def maybe_collect(*, force: bool = False) -> bool:
    """Run a full ``gc.collect()`` only occasionally, to avoid a per-rerun stall.

    Returns True if a collection actually ran. Call this once at the very bottom
    of the script instead of a bare ``gc.collect()``. Pass ``force=True`` right
    after building a heavy view (the 3D full-car figure, a big co-sim) so that
    view's transient arrays are reclaimed immediately regardless of the counter.
    """
    st = _st()
    # Without a session (headless/tests) just collect if forced, else skip.
    if st is None or not hasattr(st, "session_state"):
        if force:
            try:
                gc.collect()
                return True
            except Exception:
                return False
        return False
    try:
        n = int(st.session_state.get(_RERUN_COUNTER_KEY, 0)) + 1
        st.session_state[_RERUN_COUNTER_KEY] = n
        if force or (n % COLLECT_EVERY_N_RERUNS == 0):
            gc.collect()
            return True
    except Exception:
        # If anything about session_state misbehaves, fall back to a plain
        # collect so we still free memory rather than skipping it.
        try:
            gc.collect()
            return True
        except Exception:
            return False
    return False


def collect_now() -> None:
    """Unconditional collect — for use right after freeing a known big object."""
    try:
        gc.collect()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Plotly figure disposal                                                     #
# --------------------------------------------------------------------------- #
def release_figure(fig: Any) -> None:
    """Drop the heavy data a Plotly figure holds once Streamlit has rendered it.

    ``st.plotly_chart`` serialises the figure to JSON for the browser; after
    that the Python-side ``fig`` (with its full coordinate arrays) is dead
    weight for the rest of the rerun. Because ``st.tabs`` runs every tab, those
    dead figures otherwise coexist. Clearing ``fig.data`` lets the arrays be
    freed early. Safe on anything (no-op if it isn't a figure).
    """
    try:
        # Plotly Figure: emptying the data tuple releases the trace arrays.
        fig.data = ()
    except Exception:
        pass
    try:
        fig.layout = {}
    except Exception:
        pass


def show_and_release(fig: Any, **kwargs) -> None:
    """Render a Plotly figure with ``st.plotly_chart`` then release its data.

    A drop-in for ``st.plotly_chart(fig, **kwargs)`` at call sites that build a
    big figure they won't touch again. Keeps the exact same on-screen result
    while ensuring the Python-side arrays don't linger.
    """
    st = _st()
    if st is None:
        return
    try:
        st.plotly_chart(fig, **kwargs)
    finally:
        release_figure(fig)


# --------------------------------------------------------------------------- #
#  session_state hygiene                                                      #
# --------------------------------------------------------------------------- #
def cap_session_log(max_per_subsystem: int = ACTIVITY_MAX_PER_SUBSYSTEM) -> None:
    """Bound the activity log so a long session can't grow it without limit.

    Keeps the most recent ``max_per_subsystem`` entries per subsystem (the
    report only ever shows recent activity anyway). No-op if the log is absent.
    """
    st = _st()
    if st is None or not hasattr(st, "session_state"):
        return
    try:
        log = st.session_state.get(_ACTIVITY_KEY)
        if not isinstance(log, dict):
            return
        for sub, rows in log.items():
            if isinstance(rows, list) and len(rows) > max_per_subsystem:
                del rows[:-max_per_subsystem]     # keep newest, drop oldest
    except Exception:
        pass


def drop_state(*keys: str) -> None:
    """Delete one or more large one-shot blobs from ``session_state``.

    Use after a big byte-string / array in session_state has been consumed
    (e.g. an exported Excel file offered for download). Frees it for the rest
    of the session instead of carrying it until the tab closes.
    """
    st = _st()
    if st is None or not hasattr(st, "session_state"):
        return
    for k in keys:
        try:
            if k in st.session_state:
                del st.session_state[k]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Memory guard: shed cache before the ceiling                                #
# --------------------------------------------------------------------------- #
def memory_guard(*, soft_mb: int = SOFT_LIMIT_MB,
                 critical_mb: int = CRITICAL_MB) -> Optional[float]:
    """If RSS is nearing the limit, clear Streamlit's data cache to pull it back.

    Returns the measured RSS in MB (or None if it couldn't be read). Call once
    near the top of the script. Below ``soft_mb`` this is essentially free (one
    file read). Above it, we clear the data cache; above ``critical_mb`` we also
    force a full collection. Clearing the cache only drops recomputable results
    (the app recomputes them on demand), so correctness is unaffected.
    """
    rss = rss_mb()
    if rss is None:
        return None
    st = _st()
    try:
        if rss >= critical_mb:
            if st is not None and hasattr(st, "cache_data"):
                st.cache_data.clear()
            collect_now()
        elif rss >= soft_mb:
            if st is not None and hasattr(st, "cache_data"):
                st.cache_data.clear()
    except Exception:
        pass
    return rss
