-- ============================================================================
--  KinematiK — Analytics setup (ordered, consistent with the rewritten tab)
--  Run in: Supabase Dashboard → SQL Editor, TOP TO BOTTOM, once.
--
--  This is a convenience wrapper that runs the existing analytics SQL in the
--  ONE correct order and then pins the two settings the dashboard assumes.
--  It does NOT invent new schema — every object here already exists in
--  suspension/analytics_schema.sql; this file just guarantees ordering and
--  the labour-rate / baseline consistency the tiles depend on.
--
--  WHY ORDER MATTERS (the footgun this file removes):
--    • v_roi_summary is defined FROM v_hours_saved_by_feature.
--    • analytics_minimal_views.sql DROPS v_hours_saved_by_feature.
--      => running "minimal views" breaks live ROI. Do NOT run that file if you
--         want the ROI tile live. This setup does not include it.
--
--  Idempotent: safe to re-run. Steps 1 is your existing schema file; paste its
--  contents where indicated OR run it first, then run steps 2–4 below.
-- ============================================================================


-- ----------------------------------------------------------------------------
--  STEP 1 — Base schema (tables, seed baselines, all metric views, RLS)
--  Run the existing file first:  suspension/analytics_schema.sql
--  (creates analytics_events, feature_baselines, feature_releases,
--   analytics_config, and the v_* views incl. v_hours_saved_by_feature,
--   v_roi_summary, v_retention, v_error_rate). Do this before the steps below.
-- ----------------------------------------------------------------------------
-- >>> RUN suspension/analytics_schema.sql NOW, then continue. <<<


-- ----------------------------------------------------------------------------
--  STEP 2 — Pin the labour rate to $65/hr (schema default is $30)
--  The dashboard "Value (est.)" tile and the pre-purge baseline both assume
--  $65/hr. Without this, the DB dollar figure and the tile disagree.
-- ----------------------------------------------------------------------------
insert into analytics_config (id, labour_rate_usd_hr)
values (1, 65)
on conflict (id) do update set labour_rate_usd_hr = 65,
                               updated_at = now();


-- ----------------------------------------------------------------------------
--  STEP 3 — Historical baseline (frozen pre-purge snapshot)
--  Run the existing file:  analytics_baseline.sql
--  The rewritten tab shows this in its OWN row, clearly labelled "NOT live",
--  and no longer adds it into the live totals.
--
--  NOTE ON HONESTY: that file stores dollars_saved as a rounded headline and
--  back-derives hours_saved (= dollars / 65). It is labelled a snapshot, which
--  is fine — but treat it as historical context, not a measured figure.
-- ----------------------------------------------------------------------------
-- >>> RUN analytics_baseline.sql NOW (optional — skip if you want live-only). <<<


-- ----------------------------------------------------------------------------
--  STEP 4 — Storage guard (keep analytics_events small)
--  Run the existing file:  analytics_hard_cap.sql
--  IMPORTANT CONSEQUENCE for accuracy: this caps the events table (30-day
--  window + ~3000-row hard cap). That is why the rewritten tab labels live
--  user/returning tiles "last 30 days" and NOT "ever" — the table cannot back
--  a lifetime figure once older rows are trimmed. This is intended.
-- ----------------------------------------------------------------------------
-- >>> RUN analytics_hard_cap.sql NOW. <<<


-- ----------------------------------------------------------------------------
--  DO NOT RUN:  analytics_minimal_views.sql
--  It drops v_hours_saved_by_feature, which v_roi_summary depends on, and will
--  disable the live ROI tile (the tab will then show "ROI view not available").
-- ----------------------------------------------------------------------------


-- ----------------------------------------------------------------------------
--  VERIFY — the four things the page reads should all resolve.
-- ----------------------------------------------------------------------------
select 'labour_rate' as check, labour_rate_usd_hr::text as value
from analytics_config where id = 1
union all
select 'v_retention rows', count(*)::text from v_retention
union all
select 'v_roi_summary rows', count(*)::text from v_roi_summary
union all
select 'v_error_rate rows', count(*)::text from v_error_rate
union all
select 'baseline present', count(*)::text from analytics_baseline where id = 1;
-- Expect: labour_rate = 65; v_retention/v_roi_summary each 1 row; baseline 0 or 1.
