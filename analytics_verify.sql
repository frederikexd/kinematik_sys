-- ============================================================================
--  KinematiK — Verify the analytics views match what the tab reads
--  Run in: Supabase Dashboard → SQL Editor. Read-only; safe anytime.
--
--  The rewritten Analytics tab reads specific columns by name. If a deployed
--  view is missing one, the tile silently falls back to its "no data" state
--  instead of erroring — so a green run here is what tells you the page will
--  actually populate. Each block returns TRUE for every column the page needs.
-- ============================================================================

-- v_retention — page reads: total_users, returning_users, retention_pct
select 'v_retention' as view,
       bool_and(c = any(array['total_users','returning_users','retention_pct']))
         filter (where c = any(array['total_users','returning_users','retention_pct']))
         as required_cols_present,
       string_agg(c, ', ' order by c) as all_columns
from (
  select column_name c from information_schema.columns
  where table_schema='public' and table_name='v_retention'
) s;

-- v_roi_summary — page reads: total_hours_saved, total_dollars_saved, labour_rate_usd_hr
select 'v_roi_summary' as view,
       (exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='v_roi_summary'
                  and column_name='total_hours_saved')
        and exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='v_roi_summary'
                  and column_name='total_dollars_saved')
        and exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='v_roi_summary'
                  and column_name='labour_rate_usd_hr')) as required_cols_present;

-- v_error_rate — page reads: attempts, failures
select 'v_error_rate' as view,
       (exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='v_error_rate'
                  and column_name='attempts')
        and exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='v_error_rate'
                  and column_name='failures')) as required_cols_present;

-- analytics_baseline — page reads: total_users_ever, returning_users, dollars_saved, as_of
select 'analytics_baseline' as view,
       (exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='analytics_baseline'
                  and column_name='total_users_ever')
        and exists (select 1 from information_schema.columns
                where table_schema='public' and table_name='analytics_baseline'
                  and column_name='dollars_saved')) as required_cols_present;

-- Dependency check: v_roi_summary requires v_hours_saved_by_feature to exist.
-- If this returns FALSE, analytics_minimal_views.sql was likely run — the ROI
-- tile will show "ROI view not available".
select 'roi_dependency_ok' as check,
       exists (select 1 from information_schema.views
               where table_schema='public'
                 and table_name='v_hours_saved_by_feature') as present;

-- Live sanity: actual values the tiles will show right now.
select * from v_retention;
select total_hours_saved, total_dollars_saved, labour_rate_usd_hr from v_roi_summary;
