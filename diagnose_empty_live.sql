-- ============================================================================
--  KinematiK — Why is the live analytics row empty?
--  Run in: Supabase SQL Editor. Read-only. Paste the results back.
--
--  The dashboard's live tiles show "no live data yet" / ROI "n/a". That has two
--  possible causes with different fixes. This tells you which one you're in.
-- ============================================================================

-- (1) Do the views even exist? If any row is missing here, the schema/hardening
--     SQL hasn't successfully run (you were hitting 42P16). Fix = get the schema
--     to run cleanly.
select 'views_present' as check, string_agg(table_name, ', ' order by table_name) as found
from information_schema.views
where table_schema = 'public'
  and table_name in ('v_retention','v_roi_summary','v_error_rate',
                     'v_hours_saved_by_feature');

-- (2) Are there ANY events in the table? If this is 0, the views exist but there
--     is no post-purge data yet — the tiles are correctly empty and will fill as
--     soon as real usage is logged. Fix = nothing; use the app / let it collect.
select 'event_count' as check, count(*)::text as value from public.analytics_events;

-- (3) If events exist, what do the live views actually return right now?
--     (These are exactly what the page reads.)
select 'v_retention' as view, * from v_retention;
select 'v_roi_summary' as view, * from v_roi_summary;

-- (4) Is the ROI chain intact? v_roi_summary needs v_hours_saved_by_feature.
--     If this is FALSE, analytics_minimal_views.sql was run and dropped it —
--     that's why ROI shows "n/a". Fix = re-run analytics_schema.sql.
select 'roi_dependency_present' as check,
       exists (select 1 from information_schema.views
               where table_schema='public'
                 and table_name='v_hours_saved_by_feature') as value;

-- (5) Are feature_baselines seeded? ROI is 0/empty without them even if events
--     exist. Expect ~9 rows.
select 'feature_baselines_rows' as check, count(*)::text as value
from public.feature_baselines;
