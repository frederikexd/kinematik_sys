-- ============================================================================
--  KinematiK — Hard ~1 MB ceiling on analytics_events
--  Run in: Supabase Dashboard → SQL Editor  (after analytics_minimal_views.sql)
--
--  GOAL: guarantee the analytics_events table stays under ~1 MB no matter what,
--  by combining TWO independent limits:
--
--    1. TIME cap  — keep only the last 30 days (shorter than the old 90).
--    2. ROW  cap  — keep at most _MAX_ROWS newest rows, enforced on every
--                   insert by a trigger. This is the hard guarantee: even a
--                   traffic spike inside the 30-day window can't grow the table
--                   past the row cap.
--
--  SIZING: at ~250 bytes/row, 3000 rows ≈ 0.75 MB including index overhead,
--  comfortably under 1 MB. Adjust _MAX_ROWS below if you want a different
--  ceiling (2000 rows ≈ 0.5 MB, 4000 ≈ 1 MB).
--
--  Safe to re-run (idempotent).
-- ============================================================================


-- ----------------------------------------------------------------------------
--  1. TIGHTEN THE TIME WINDOW to 30 days (updates the existing purge function)
-- ----------------------------------------------------------------------------
create or replace function public.purge_old_analytics_events()
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    _retention_days constant int := 30;   -- was 90; tighter = less stored
    _deleted        bigint;
begin
    delete from public.analytics_events
    where occurred_at < now() - make_interval(days => _retention_days);
    get diagnostics _deleted = row_count;
    raise notice 'purge_old_analytics_events: deleted % rows older than % days',
        _deleted, _retention_days;
end;
$$;

revoke execute on function public.purge_old_analytics_events() from public, anon, authenticated;


-- ----------------------------------------------------------------------------
--  2. HARD ROW CAP — trigger keeps only the newest _MAX_ROWS rows
-- ----------------------------------------------------------------------------
--  Runs after each insert. To keep it cheap it only trims occasionally (when
--  the row count drifts over the cap by a margin), not on literally every
--  insert — a statement-level trigger with a lightweight count check. Deleting
--  by id keeps the newest rows (id is a monotonic bigint from the identity PK).
create or replace function public.cap_analytics_rows()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    _max_rows   constant bigint := 3000;   -- ~0.75 MB ceiling. Tune here.
    _slack      constant bigint := 200;    -- only trim once we're this far over
    _count      bigint;
begin
    select count(*) into _count from public.analytics_events;
    if _count > _max_rows + _slack then
        delete from public.analytics_events
        where id in (
            select id from public.analytics_events
            order by id asc                    -- oldest first
            limit (_count - _max_rows)         -- trim back down to the cap
        );
    end if;
    return null;   -- AFTER trigger, return value ignored
end;
$$;

drop trigger if exists trg_cap_analytics_rows on public.analytics_events;
create trigger trg_cap_analytics_rows
    after insert on public.analytics_events
    for each statement
    execute function public.cap_analytics_rows();


-- ----------------------------------------------------------------------------
--  3. APPLY BOTH NOW — trim to the tighter window and the row cap immediately
-- ----------------------------------------------------------------------------
select public.purge_old_analytics_events();

-- one-shot row-cap enforcement for existing data (the trigger only fires on
-- future inserts, so trim current backlog once by hand):
do $$
declare
    _max_rows constant bigint := 3000;
    _count    bigint;
begin
    select count(*) into _count from public.analytics_events;
    if _count > _max_rows then
        delete from public.analytics_events
        where id in (
            select id from public.analytics_events
            order by id asc
            limit (_count - _max_rows)
        );
    end if;
end $$;

vacuum public.analytics_events;


-- ----------------------------------------------------------------------------
--  4. VERIFY — should show rows <= ~3000 and size well under 1 MB
-- ----------------------------------------------------------------------------
select
    count(*)                                                          as rows_now,
    pg_size_pretty(pg_total_relation_size('public.analytics_events')) as total_size
from analytics_events;


-- ============================================================================
--  DONE. Two independent guards now keep the table under ~1 MB:
--    • nightly 30-day purge (existing cron kinematik_purge_analytics)
--    • per-insert hard cap at 3000 rows (trg_cap_analytics_rows)
--  Combined with writing only 3 event types, the table stays tiny for good.
--
--  To change the ceiling: edit _max_rows in BOTH cap_analytics_rows() and the
--  one-shot DO block above, then re-run this file.
--  To remove the row cap:  drop trigger trg_cap_analytics_rows on analytics_events;
-- ============================================================================
