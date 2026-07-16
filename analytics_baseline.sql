-- ============================================================================
--  KinematiK — Preserve the pre-purge historical baseline
--  Run in: Supabase Dashboard → SQL Editor
--
--  The original analytics_events history was truncated to fix the 500 MB
--  overage, so the live views (v_retention, v_roi_summary) now start from zero
--  and rebuild going forward. But the HEADLINE FIGURES the dashboard showed at
--  that point were captured from the dashboard itself and are stored here as a
--  fixed, one-row baseline so they remain visible and citable for the pitch.
--
--  These are a historical snapshot as of 2026-07-10 — NOT live data. The tab
--  shows them clearly labelled as a baseline, above the (rebuilding) live tiles.
--
--  Safe to re-run (idempotent upsert on id = 1).
-- ============================================================================

create table if not exists analytics_baseline (
    id                  int primary key default 1 check (id = 1),
    as_of               date        not null,
    total_users_ever    bigint      not null,
    returning_users     bigint      not null,
    retention_pct       numeric     not null,
    hours_saved         numeric     not null,
    dollars_saved       numeric     not null,
    note                text,
    created_at          timestamptz not null default now()
);

-- Figures read directly from the dashboard recording taken just before the
-- purge (2026-07-10). Dollars-saved uses the ROI headline; hours-saved is
-- back-derived at the $65/hr labour rate the tab uses ($124k ÷ 65 ≈ 1908 h).
insert into analytics_baseline
    (id, as_of, total_users_ever, returning_users, retention_pct,
     hours_saved, dollars_saved, note)
values
    (1, date '2026-07-10',
     778,          -- total users ever
     482,          -- returning users
     62.0,         -- retention %
     1908,         -- hours saved — ESTIMATE, back-derived as $124,000 / $65,
                   --   not an independently measured count of hours.
     124000,       -- dollars saved (ROI headline, rounded estimate)
     'Pre-purge usage snapshot recorded from the live dashboard on 2026-07-10. '
     || 'Context: traffic outgrew the database storage tier, so the events '
     || 'table was purged as a capacity measure to keep the app running — the '
     || 'purge was operational, not a correction. The user counts (778 total, '
     || '482 returning) are real pre-purge usage observed on the dashboard at '
     || 'that time. The dollar figure is a rounded ROI ESTIMATE, and hours '
     || 'saved is back-derived from it (dollars / $65-per-hr), not a measured '
     || 'value — both rest on the per-feature time assumptions in '
     || 'feature_baselines (all marked confidence=estimate). Shown as a fixed, '
     || 'dated reference, separate from the rebuilding live tiles.')
on conflict (id) do update set
    as_of            = excluded.as_of,
    total_users_ever = excluded.total_users_ever,
    returning_users  = excluded.returning_users,
    retention_pct    = excluded.retention_pct,
    hours_saved      = excluded.hours_saved,
    dollars_saved    = excluded.dollars_saved,
    note             = excluded.note;

-- read policy so the app (anon key) can display it
alter table analytics_baseline enable row level security;
grant select on analytics_baseline to anon, authenticated;
do $$
begin
    if not exists (
        select 1 from pg_policies
        where tablename = 'analytics_baseline' and policyname = 'baseline_read'
    ) then
        create policy baseline_read on analytics_baseline for select using (true);
    end if;
end $$;

-- verify
select as_of, total_users_ever, returning_users, retention_pct,
       hours_saved, dollars_saved
from analytics_baseline where id = 1;
