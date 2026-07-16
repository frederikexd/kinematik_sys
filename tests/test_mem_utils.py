# ============================================================================
#  KinematiK — Formula SAE suspension & vehicle dynamics toolkit
#  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
#  Open source. Original author: Frederik Thio, creator of KinematiK.
# ============================================================================
"""
test_mem_utils.py — the RAM-hygiene helpers behave and never crash the app
==========================================================================

These lock in the memory-management contract that keeps the Streamlit app under
the ~1 GB Community-Cloud ceiling:

  * RSS can be read (or degrades to None) without raising.
  * Throttled collection runs occasionally, not on every call.
  * A Plotly figure's heavy data is actually released.
  * session_state hygiene helpers no-op safely when there's no session.
  * Nothing here raises into the app, ever — a memory helper must not be the
    thing that crashes a tab.
"""
import gc

import pytest

from suspension import mem_utils as m


# --------------------------------------------------------------------------- #
#  RSS + guard                                                                #
# --------------------------------------------------------------------------- #
def test_rss_mb_returns_number_or_none():
    v = m.rss_mb()
    assert v is None or (isinstance(v, float) and v > 0)


def test_memory_guard_returns_rss_and_never_raises():
    # Headless (no streamlit session) it should just read RSS and return it.
    v = m.memory_guard()
    assert v is None or isinstance(v, float)


# --------------------------------------------------------------------------- #
#  Throttled collection                                                       #
# --------------------------------------------------------------------------- #
def test_forced_collect_runs_headless():
    assert m.maybe_collect(force=True) is True


def test_unforced_collect_is_noop_headless():
    # With no streamlit session, an unforced call should skip (returns False)
    # rather than collecting on every call.
    assert m.maybe_collect() is False


def test_collect_now_never_raises():
    m.collect_now()   # smoke: must not raise


# --------------------------------------------------------------------------- #
#  Figure release                                                             #
# --------------------------------------------------------------------------- #
def test_release_figure_empties_plotly_data():
    go = pytest.importorskip("plotly.graph_objects")
    fig = go.Figure(data=[go.Scatter(x=list(range(1000)), y=list(range(1000)))])
    assert len(fig.data) == 1
    m.release_figure(fig)
    assert len(fig.data) == 0


def test_release_figure_safe_on_non_figure():
    class NotAFigure:
        pass
    m.release_figure(NotAFigure())   # must not raise
    m.release_figure(None)


# --------------------------------------------------------------------------- #
#  session_state hygiene (headless: safe no-ops)                              #
# --------------------------------------------------------------------------- #
def test_session_helpers_noop_without_session():
    m.cap_session_log()
    m.drop_state("anything", "at", "all")
    # No assertion needed: the contract is "does not raise headless".


# --------------------------------------------------------------------------- #
#  Tunables are readable and sane                                             #
# --------------------------------------------------------------------------- #
def test_thresholds_are_ordered_under_the_ceiling():
    assert m.SOFT_LIMIT_MB < m.CRITICAL_MB < m.HARD_LIMIT_MB
    assert m.COLLECT_EVERY_N_RERUNS >= 1
