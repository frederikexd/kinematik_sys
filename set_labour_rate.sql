-- ============================================================================
--  KinematiK — Set labour rate to $65/hr
--  Run in: Supabase Dashboard → SQL Editor
--
--  Dollars-saved is valued at a labour rate stored in analytics_config. The
--  schema default was $30/hr; this sets it to $65/hr so the live ROI figure
--  (v_roi_summary.total_dollars_saved) matches the $65/hr basis used for the
--  historical baseline. The dashboard tile reads this same value, so after this
--  runs, the database and the tile agree.
--
--  Safe to re-run (idempotent).
-- ============================================================================

update analytics_config
set labour_rate_usd_hr = 65,
    updated_at = now()
where id = 1;

-- If the config row doesn't exist yet, create it at $65.
insert into analytics_config (id, labour_rate_usd_hr)
values (1, 65)
on conflict (id) do update set labour_rate_usd_hr = 65;

-- verify
select id, labour_rate_usd_hr, avoided_licence_usd, updated_at
from analytics_config where id = 1;
-- expected: labour_rate_usd_hr = 65
