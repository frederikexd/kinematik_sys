KINEMATIK — MINIMAL ANALYTICS (lowest storage & egress)
========================================================
A stripped-down analytics tab that shows only what matters and
touches Supabase as little as possible.

WHAT THE TAB NOW SHOWS (nothing else):
  • Hours saved / Dollars saved   (ROI)
  • Total users / Returning %      (retention)
  • Error rate                     (reliability)

WHAT IT NO LONGER DOES:
  • No per-feature tables, individual-user lists, foot traffic,
    adoption funnel, latency charts, vs-alternatives pricing,
    or debug panels.
  • Fetches 3 Supabase views instead of 11  (~1/4 the read egress).
  • Writes only 3 event types instead of 10 — session_start,
    workflow_complete, error. Everything else is dropped at the
    source, so the analytics_events table grows as slowly as
    possible.


-----------------------------------------------------------------
FILES & WHERE THEY GO
-----------------------------------------------------------------
  streamlit_app.py            ->  streamlit_app.py            (replace)
  analytics.py                ->  suspension/analytics.py     (replace)
  analytics_cache.py          ->  suspension/analytics_cache.py (new)
  project.py                  ->  suspension/project.py       (replace)
  analytics_minimal_views.sql ->  run in Supabase SQL Editor


-----------------------------------------------------------------
DEPLOY
-----------------------------------------------------------------
  1. Copy the four .py files into place.
  2. Run analytics_minimal_views.sql in Supabase — drops the 8
     unused views, keeps v_roi_summary / v_retention / v_error_rate.
     (Verify: the final SELECT should return exactly those 3.)
  3. Set KINEMATIK_ANALYTICS = "on" in your Streamlit secrets.
  4. Redeploy / restart.


-----------------------------------------------------------------
STORAGE MATH
-----------------------------------------------------------------
  Before: 10 event types, all high-frequency ones written in full.
  Now:    3 event types, and only the low-frequency ones
          (a session start, a completed workflow, an error) —
          typically a handful of rows per user per visit.
  Plus the 90-day auto-purge cron already running trims anything
  older nightly. Table stays tiny indefinitely.


-----------------------------------------------------------------
TO BRING BACK A FULL METRIC LATER
-----------------------------------------------------------------
  1. Re-enable its event type in _SAMPLE_RATES (analytics.py).
  2. Re-create the view it needs (analytics_schema.sql /
     analytics_hardening.sql).
  3. Re-add its fetch + render block in the Analytics tab.


-----------------------------------------------------------------
HARD <1 MB CEILING  (analytics_hard_cap.sql)
-----------------------------------------------------------------
Run this AFTER analytics_minimal_views.sql. It guarantees the
table stays under ~1 MB with two independent guards:

  1. Retention window shortened 90 → 30 days (updates the
     existing nightly purge function; cron already scheduled).
  2. Hard row cap: an AFTER-INSERT trigger keeps at most 3000
     rows (~0.75 MB incl. indexes). Even a traffic spike can't
     exceed it — oldest rows are trimmed automatically.

Verified: simulated 5000 inserts → table held at ~3000 rows,
newest rows retained. Ceiling holds.

Sizing (at ~250 bytes/row):
   2000 rows ≈ 0.5 MB
   3000 rows ≈ 0.75 MB   ← default
   4000 rows ≈ 1.0 MB
Change _max_rows in analytics_hard_cap.sql to adjust.

DEPLOY ORDER (SQL, in Supabase editor):
   1. analytics_minimal_views.sql   (drop unused views)
   2. analytics_hard_cap.sql        (30-day + 3000-row cap)
