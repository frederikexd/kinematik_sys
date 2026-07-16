-- ============================================================================
--  fix_missing_features.sql
--  Fix: insert or update on "analytics_events" violates foreign key
--       constraint "ae_feature_fk"   (SQLSTATE 23503)
--
--  CAUSE
--  -----
--  ae_feature_fk (added in analytics_hardening.sql) requires every
--  analytics_events.feature to exist in the known_features allow-list. The
--  app's _TAB_META (streamlit_app.py) logs two feature ids that were never
--  seeded into that list:
--      'docs'   — Documentation
--      'frames' — Frames & Datums
--  so every analytics event from those two tabs is rejected.
--
--  FIX
--  ---
--  Add the two missing rows to the allow-list. Because the design uses a FK to
--  a table (not a CHECK), this is a plain data insert — no schema migration.
--  Idempotent: safe to run more than once.
--
--  This same fix is now folded into analytics_hardening.sql's seed, so a fresh
--  run_all.sql on a new database won't hit this. Run THIS file on an existing
--  database that already has the constraint but not the rows.
-- ============================================================================

insert into known_features (feature, label, is_tab, reactive_only) values
    ('docs',   'Documentation',    true, false),
    ('frames', 'Frames & Datums',  true, false)
on conflict (feature) do update
    set label         = excluded.label,
        is_tab        = excluded.is_tab,
        reactive_only = excluded.reactive_only;

-- ----------------------------------------------------------------------------
--  OPTIONAL — ROI baselines for the two tabs. Without these, the coverage view
--  flags them as "NO BASELINE" (usage logs, but hours-saved can't compute).
--  Adjust the minute estimates to your real numbers, or skip if you don't
--  track hours-saved for these tabs.
-- ----------------------------------------------------------------------------
-- insert into feature_baselines
--     (feature, label, manual_minutes, in_tool_minutes, alternative)
-- values
--     ('docs',   'Documentation',   30, 5, 'manual doc assembly / Word'),
--     ('frames', 'Frames & Datums', 20, 4, 'manual datum setup in CAD')
-- on conflict (feature) do nothing;

-- ----------------------------------------------------------------------------
--  VERIFY — should return zero rows once the fix is applied (any event whose
--  feature id still isn't in the allow-list).
-- ----------------------------------------------------------------------------
-- select * from v_orphaned_feature_events;
