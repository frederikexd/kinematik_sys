# Minimal Analytics — Deploy Notes

This build has the analytics system trimmed to the essentials, tuned to keep
the Supabase `analytics_events` table under ~1 MB.

## What changed vs the full app
- **streamlit_app.py** — Analytics tab shows only: Hours/Dollars saved (ROI),
  Total users / Returning % (retention), Error rate (reliability). All other
  panels removed. Fetches 3 Supabase views (cached 5 min) instead of 11.
- **suspension/analytics.py** — writes only 3 event types (`session_start`,
  `workflow_complete`, `error`); all others dropped at source to minimise rows.
- **suspension/analytics_cache.py** — NEW. 5-minute read-through cache for views.
- **suspension/project.py** & **project.py** — version-keyed cache for the
  project blob (only re-fetches the ~MB document when it actually changes).
- **analytics_buffer.jsonl** — emptied (was 147 stale events of now-unused types).

## SQL to run in Supabase (in order)
1. `analytics_retention_cron.sql` — nightly purge+vacuum cron (if not already run)
2. `analytics_minimal_views.sql` — drop the 8 unused views (keep 3)
3. `analytics_hard_cap.sql` — 30-day window + hard 3000-row cap (~0.75 MB ceiling)
   NOTE: the `VACUUM` line must be run on its OWN (Supabase wraps scripts in a
   transaction and VACUUM can't run inside one). Run everything else first, then
   run `vacuum public.analytics_events;` as a separate single statement — or just
   skip it, since the nightly cron vacuums anyway.

## Turn analytics on
Set `KINEMATIK_ANALYTICS = "on"` in Streamlit secrets (or remove the key).
Leave it `"off"` to keep telemetry fully paused.

## Database size guarantee
Three stacked limits keep `analytics_events` under ~1 MB:
- only 3 low-frequency event types are written
- 30-day rolling purge (nightly cron)
- hard 3000-row cap (per-insert trigger) ≈ 0.75 MB max

## Historical baseline (preserved pre-purge figures)
The original events were truncated to fix the 500MB overage. These validated
totals were captured from the dashboard right before the wipe and are shown in
the Analytics tab as a fixed "Historical baseline" panel above the live tiles:
  - Total users ever: 778
  - Returning: 482 (62%)
  - Dollars saved: ~$124,000
Run `analytics_baseline.sql` in Supabase to store them. The live tiles below the
baseline rebuild from the purge date forward.
