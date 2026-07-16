-- ============================================================================
--  KinematiK — Minimal analytics: drop the views the lean tab doesn't use
--  Run in: Supabase Dashboard → SQL Editor
--
--  The minimal Analytics tab reads ONLY three views:
--      v_roi_summary      (hours/dollars saved)
--      v_retention        (total users + returning %)
--      v_error_rate       (reliability)
--
--  Every other analytics view can be dropped. Views store no data themselves
--  (they're saved queries), so dropping them frees only a little catalog space
--  — but it also stops anything from accidentally querying the heavier ones,
--  and keeps your schema honest about what's actually in use.
--
--  The underlying analytics_events table is untouched; the three kept views
--  still work. Safe to re-run (IF EXISTS). If you later want the full dashboard
--  back, re-run analytics_schema.sql + analytics_hardening.sql to recreate them.
-- ============================================================================

drop view if exists v_hours_saved_by_feature     cascade;
drop view if exists v_foot_traffic_daily         cascade;
drop view if exists v_feature_use                cascade;
drop view if exists v_adoption_funnel            cascade;
drop view if exists v_latency_by_feature         cascade;
drop view if exists v_time_to_first_result       cascade;
drop view if exists v_comparison_to_alternatives cascade;
drop view if exists v_individual_use             cascade;

-- Optional hardening/diagnostic views, if present — also unused by the lean tab.
drop view if exists v_instrumentation_coverage   cascade;
drop view if exists v_visitor_id_health          cascade;
drop view if exists v_write_health               cascade;

-- ----------------------------------------------------------------------------
--  Confirm the three kept views still exist and the rest are gone.
-- ----------------------------------------------------------------------------
select table_name
from information_schema.views
where table_schema = 'public'
  and table_name like 'v_%'
order by table_name;

-- Expected result: exactly three rows —
--   v_error_rate
--   v_retention
--   v_roi_summary
