-- ============================================================================
--  KinematiK — Why is the live row empty?  (single result set)
--  Run in Supabase SQL Editor. Returns ONE table so it exports to one CSV.
--  Paste the whole result back.
-- ============================================================================
with checks as (
    select 1 as ord, 'events_in_table' as check_name,
           (select count(*)::text from public.analytics_events) as value
    union all
    select 2, 'v_retention_exists',
           (select exists(select 1 from information_schema.views
             where table_schema='public' and table_name='v_retention')::text)
    union all
    select 3, 'v_roi_summary_exists',
           (select exists(select 1 from information_schema.views
             where table_schema='public' and table_name='v_roi_summary')::text)
    union all
    select 4, 'v_hours_saved_by_feature_exists',
           (select exists(select 1 from information_schema.views
             where table_schema='public' and table_name='v_hours_saved_by_feature')::text)
    union all
    select 5, 'v_error_rate_exists',
           (select exists(select 1 from information_schema.views
             where table_schema='public' and table_name='v_error_rate')::text)
    union all
    select 6, 'feature_baselines_rows',
           (select count(*)::text from public.feature_baselines)
    union all
    select 7, 'workflow_complete_events',
           (select count(*)::text from public.analytics_events
            where event_type='workflow_complete')
    union all
    select 8, 'session_start_events',
           (select count(*)::text from public.analytics_events
            where event_type='session_start')
    union all
    select 9, 'distinct_sessions',
           (select count(distinct session_id)::text from public.analytics_events)
    union all
    select 10, 'earliest_event',
           (select coalesce(min(occurred_at)::text,'(none)') from public.analytics_events)
    union all
    select 11, 'latest_event',
           (select coalesce(max(occurred_at)::text,'(none)') from public.analytics_events)
    union all
    select 12, 'retention_total_users',
           (select coalesce(max(total_users)::text,'(view empty/err)')
            from (select total_users from v_retention limit 1) x)
    union all
    select 13, 'roi_total_hours',
           (select coalesce(max(total_hours_saved)::text,'(view empty/err)')
            from (select total_hours_saved from v_roi_summary limit 1) x)
)
select ord, check_name, value from checks order by ord;
