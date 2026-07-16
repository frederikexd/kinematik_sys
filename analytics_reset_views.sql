-- ============================================================================
--  KinematiK — Reset analytics views before re-running the schema
--  Run in: Supabase SQL Editor, IMMEDIATELY BEFORE suspension/analytics_schema.sql
--
--  WHY: `CREATE OR REPLACE VIEW` cannot change a view's column set — Postgres
--  raises "42P16: cannot drop columns from view" when an existing view has a
--  different shape than the new definition (a leftover from an earlier schema
--  version or a partial run). Dropping the views first clears that; the schema
--  then recreates every one of them.
--
--  SAFE: views store no data (they are saved queries). Dropping and recreating
--  them changes nothing in analytics_events or any table. `cascade` handles the
--  dependency chain (v_roi_summary depends on v_hours_saved_by_feature, etc.).
--  `if exists` makes it safe to run even if some views are absent.
--
--  Idempotent. Order below is not critical because of `cascade`, but they are
--  listed dependents-first for clarity.
-- ============================================================================

drop view if exists v_roi_summary                cascade;
drop view if exists v_hours_saved_by_feature     cascade;
drop view if exists v_comparison_to_alternatives cascade;
drop view if exists v_adoption_funnel            cascade;
drop view if exists v_time_to_first_result       cascade;
drop view if exists v_retention                  cascade;
drop view if exists v_error_rate                 cascade;
drop view if exists v_latency_by_feature         cascade;
drop view if exists v_feature_delivery           cascade;
drop view if exists v_feature_use                cascade;
drop view if exists v_individual_use             cascade;
drop view if exists v_active_members_weekly      cascade;
drop view if exists v_foot_traffic_daily         cascade;

-- Also drop the hardening/diagnostic views if they exist, so a later run of
-- analytics_hardening.sql recreates them cleanly too.
drop view if exists v_instrumentation_coverage   cascade;
drop view if exists v_visitor_id_health          cascade;
drop view if exists v_write_health               cascade;
drop view if exists v_retention_recovered        cascade;
drop view if exists v_instrumentation_health     cascade;
drop view if exists v_orphaned_feature_events    cascade;

-- Confirm none of the v_* views remain (expect zero rows).
select table_name
from information_schema.views
where table_schema = 'public' and table_name like 'v_%'
order by table_name;
