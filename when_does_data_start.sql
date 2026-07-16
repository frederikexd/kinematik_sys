-- ============================================================================
--  KinematiK — When does the CURRENTLY VISIBLE analytics data actually start?
--  Run in: Supabase SQL Editor. Read-only.
--
--  This does NOT return July 10. July 10 was the purge (old events deleted).
--  The live data started collecting AFTER that, and because analytics_hard_cap
--  enforces a rolling 30-day + 3000-row window, the earliest visible event
--  moves forward over time. This query reads the true current start.
-- ============================================================================

select
    min(occurred_at)                            as earliest_event,
    max(occurred_at)                            as latest_event,
    min(occurred_at)::date                      as data_starts,
    (max(occurred_at)::date - min(occurred_at)::date) as span_days,
    count(*)                                     as rows_now,
    -- how the window is bounded right now:
    case
      when min(occurred_at) > now() - interval '30 days'
        then 'row-cap bound (hit 3000 rows before 30 days)'
      else 'time bound (30-day retention)'
    end                                         as window_limited_by
from public.analytics_events;

-- Context: the purge/baseline date for comparison.
select as_of as purge_baseline_date
from public.analytics_baseline
where id = 1;
