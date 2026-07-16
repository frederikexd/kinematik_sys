-- ============================================================================
--  KinematiK — Analytics rolling-retention (auto-purge) migration
--  Run in: Supabase Dashboard → SQL Editor
--
--  WHAT THIS DOES
--  --------------
--  Schedules a daily job that deletes analytics_events rows older than a
--  retention window (default 90 days) and VACUUMs the table so the space is
--  physically reclaimed. This is what stops the append-only log from ever
--  growing back into the 500MB free-tier ceiling — you set it once and the
--  table self-trims forever.
--
--  WHY 90 DAYS
--  -----------
--  Long enough that retention / returning-user metrics (which need multi-week
--  history) stay meaningful, short enough that the table stays tiny. Change the
--  interval in ONE place below (_retention_days) if you want 30 or 180.
--
--  SAFE TO RE-RUN: idempotent. Re-running re-registers the same job, it does
--  not create duplicates (we unschedule any prior copy first).
-- ============================================================================


-- ----------------------------------------------------------------------------
--  1. ENABLE pg_cron
-- ----------------------------------------------------------------------------
--  pg_cron is available on Supabase but must be enabled once. It lives in the
--  `cron` schema. (On Supabase this is also toggleable via Database → Extensions,
--  but enabling it here in SQL is idempotent and self-documenting.)
create extension if not exists pg_cron;


-- ----------------------------------------------------------------------------
--  2. THE PURGE FUNCTION — delete old rows, then reclaim space
-- ----------------------------------------------------------------------------
--  Kept as a function (not an inline cron command) so the retention window lives
--  in exactly one place and the job body is a single clean call. SECURITY
--  DEFINER so the cron runner (which executes as the job owner) can delete
--  regardless of RLS; search_path pinned to avoid the mutable-search-path risk
--  that Supabase's linter (correctly) flags on SECURITY DEFINER functions.
create or replace function public.purge_old_analytics_events()
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    _retention_days constant int := 90;   -- <<< change window here if needed
    _deleted        bigint;
begin
    delete from public.analytics_events
    where occurred_at < now() - make_interval(days => _retention_days);

    get diagnostics _deleted = row_count;

    -- Reclaim the pages from the deleted rows. Plain VACUUM (not FULL) is safe to
    -- run online and doesn't take an exclusive lock. On the free tier this is
    -- what actually returns the space to your quota after a DELETE.
    -- NOTE: VACUUM cannot run inside the function's implicit transaction block,
    -- so we can't call it here directly. Instead the daily job below runs the
    -- delete via this function and a separate VACUUM step. We keep the delete in
    -- the function for the single-source-of-truth retention window.
    raise notice 'purge_old_analytics_events: deleted % rows older than % days',
        _deleted, _retention_days;
end;
$$;

comment on function public.purge_old_analytics_events is
  'Deletes analytics_events older than the retention window (90 days). Called
   daily by the pg_cron job registered in analytics_retention_cron.sql. VACUUM
   is run as a separate cron step because it cannot execute inside a function.';

-- Don't leave this callable by the public API roles — only the cron runner and
-- the table owner should invoke it.
revoke execute on function public.purge_old_analytics_events() from public, anon, authenticated;


-- ----------------------------------------------------------------------------
--  3. SCHEDULE THE DAILY JOBS
-- ----------------------------------------------------------------------------
--  Two steps at 03:15 UTC (low-traffic): (a) delete old rows via the function,
--  (b) VACUUM to reclaim space. Unschedule any prior copies first so re-running
--  this file doesn't stack duplicate jobs.

-- (a) remove old jobs if they exist (no error if they don't)
do $$
declare
    _jid bigint;
begin
    for _jid in
        select jobid from cron.job
        where jobname in ('kinematik_purge_analytics', 'kinematik_vacuum_analytics')
    loop
        perform cron.unschedule(_jid);
    end loop;
end $$;

-- (b) daily delete of old rows — 03:15 UTC
select cron.schedule(
    'kinematik_purge_analytics',
    '15 3 * * *',
    $$select public.purge_old_analytics_events();$$
);

-- (c) daily vacuum 5 minutes later, once the delete has finished — 03:20 UTC.
--     VACUUM must be its own top-level statement, which is why it's a separate
--     job rather than a line inside the function.
select cron.schedule(
    'kinematik_vacuum_analytics',
    '20 3 * * *',
    $$vacuum public.analytics_events;$$
);


-- ----------------------------------------------------------------------------
--  4. RUN ONCE NOW — don't wait until tonight for the first trim
-- ----------------------------------------------------------------------------
select public.purge_old_analytics_events();
vacuum public.analytics_events;


-- ----------------------------------------------------------------------------
--  5. VERIFY — confirm the jobs are registered and see current table state
-- ----------------------------------------------------------------------------
select jobid, jobname, schedule, active
from cron.job
where jobname like 'kinematik_%'
order by jobname;

select
    count(*)                                                as rows_remaining,
    min(occurred_at)                                        as oldest_event,
    max(occurred_at)                                        as newest_event,
    pg_size_pretty(pg_total_relation_size('public.analytics_events')) as table_size
from analytics_events;


-- ============================================================================
--  DONE.
--  From now on the table auto-trims to a 90-day window every night. Combined
--  with the read-side cache and the event sampling in analytics.py, this keeps
--  analytics running on the free tier indefinitely.
--
--  To change the window later: edit _retention_days in
--  purge_old_analytics_events() and re-run just that CREATE OR REPLACE FUNCTION.
--  To pause auto-purge:   select cron.unschedule('kinematik_purge_analytics');
--                         select cron.unschedule('kinematik_vacuum_analytics');
-- ============================================================================
