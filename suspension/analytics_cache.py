# ============================================================================
#  KinematiK — cached read-through for the analytics dashboard views
#
#  Wraps analytics.fetch_view in an @st.cache_data layer with a short TTL so the
#  Analytics tab doesn't re-query Supabase on every Streamlit rerun. The numbers
#  don't need to be live-to-the-second, so we serve them from cache and only hit
#  Supabase once per TTL window per view — turning "N reads per rerun" into "one
#  read every few minutes". "Refresh now" (clear_cache) busts it on demand.
#
#  IMPORTANT: the cached function is defined ONCE at module level (below), not
#  re-created inside the wrapper on every call. Decorating a freshly-defined
#  closure on each call makes st.cache_data's memoisation and .clear() behave
#  unpredictably (stale entries, refresh not taking effect). Module-level
#  definition is what makes both caching AND clearing reliable.
# ============================================================================

from __future__ import annotations

# TTL in seconds. 300 = 5 minutes. Raise to reduce egress further (older
# numbers), lower for fresher data at the cost of more reads. Single tuning knob.
_CACHE_TTL_SECONDS = 300

try:
    import streamlit as st
    _HAVE_ST = True
except Exception:
    _HAVE_ST = False


if _HAVE_ST:
    @st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
    def _cached_fetch(view_name: str) -> list[dict]:
        # Imported lazily so this module stays importable without a configured
        # analytics backend. Defined once, at import time, so st.cache_data has a
        # single stable entry per view_name that clear_cache() can reliably bust.
        from suspension import analytics as _ax
        return _ax.fetch_view(view_name)


def fetch_view_cached(view_name: str) -> list[dict]:
    """Cached read-through to analytics.fetch_view.

    With Streamlit present, results are memoised for _CACHE_TTL_SECONDS via a
    module-level cached function. Without Streamlit (plain scripts / tests) this
    degrades to a direct, uncached call. Honours the kill-switch automatically:
    the underlying fetch_view returns [] without touching Supabase when
    KINEMATIK_ANALYTICS=off.
    """
    if _HAVE_ST:
        return _cached_fetch(view_name)
    from suspension import analytics as _ax
    return _ax.fetch_view(view_name)


def clear_cache() -> None:
    """Bust the cached view results so the next fetch_view_cached call hits
    Supabase live. Wire to a 'Refresh now' button:

        if st.button("🔄 Refresh now"):
            from suspension.analytics_cache import clear_cache
            clear_cache()
            st.rerun()

    Clears the specific cached function when possible (leaving other caches
    intact), falling back to a global clear.
    """
    if not _HAVE_ST:
        return
    try:
        # Preferred: clear only this function's memoised entries.
        _cached_fetch.clear()
    except Exception:
        try:
            st.cache_data.clear()
        except Exception:
            pass
