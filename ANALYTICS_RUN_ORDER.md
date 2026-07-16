# Analytics — what to run

Two categories. **SQL** you execute in Supabase. **Python** just needs to exist
in the repo — the app runs as one Streamlit process, so you don't run Python
files individually; you run the app.

---

## SQL — run in Supabase SQL Editor, in THIS order

| # | File | What it does | Required? |
|---|------|--------------|-----------|
| 1 | `suspension/analytics_schema.sql` | Tables, all `v_*` views, RLS. Foundation. | **Yes** |
| 2 | `set_labour_rate.sql` | Sets rate to $65/hr (default is $30). | **Yes** |
| 3 | `analytics_baseline.sql` | Loads the 778/482 pre-purge snapshot. | Yes (keep baseline) |
| 4 | `suspension/analytics_hardening.sql` | Debug/diagnostic views for `?debug=1`. | Optional |
| 5 | `analytics_hard_cap.sql` | 30-day + 3000-row cap. Prevents another purge. | **Yes — run LAST** |
| 6 | `analytics_verify.sql` | Read-only check that views expose the right columns. | Recommended |

`analytics_setup.sql` is a guided wrapper for steps 1–3 + 5 if you'd rather
follow one file.

### Do NOT run
- **`analytics_minimal_views.sql`** — drops `v_hours_saved_by_feature`, which
  `v_roi_summary` is built from. Running it disables the live ROI (dollars) tile.
- **`analytics_retention_cron.sql`** — only if you specifically want a scheduled
  pg_cron purge. **Conflict warning:** it redefines `purge_old_analytics_events()`
  at a **90-day** window, overwriting the **30-day** version from
  `analytics_hard_cap.sql`. If you run both, run the cron file FIRST and the
  hard-cap LAST, or your retention silently reverts to 90 days.

### Why order matters
- `v_roi_summary` is defined `FROM v_hours_saved_by_feature` → schema (step 1)
  must come before anything that reads ROI.
- Step 5 must be last so its tighter 30-day purge wins over any other definition.

---

## Python — nothing to "run" individually

The app is one process. Launch it the normal way (e.g. `streamlit run
streamlit_app.py`). For that to work the whole `suspension/` package must be
present — it is, in this repo.

The only files specific to analytics (already here, unchanged except the page):
- `streamlit_app.py` — the Analytics tab (edited: baseline vs live separated).
- `suspension/analytics.py` — event logging + `fetch_view` backend.
- `suspension/analytics_cache.py` — 5-min read-through cache over the views.
- `suspension/visitor_id.py` — durable per-browser id for retention.

Environment: set `SUPABASE_URL` and `SUPABASE_KEY` (anon key) so the app reads
live data. Without them the tab falls back to the local `analytics_buffer.jsonl`.
