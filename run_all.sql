-- ############################################################################
-- #  KinematiK — consolidated Supabase setup: run_all.sql
-- #  Generated 2026-07-15. Paste into the Supabase SQL editor and run once.
-- #
-- #  Every included script is idempotent (IF NOT EXISTS / CREATE OR REPLACE),
-- #  so re-running this whole file is safe.
-- #
-- #  ORDER (dependency-driven; do not reshuffle):
-- #    1  analytics_schema.sql            base tables (retrofitted later)
-- #    2  myth_schema.sql                 base tables (retrofitted later)
-- #    3  workspace_isolation.sql         TENANCY SPINE — required for accounts
-- #    4  workspace_members_rpc.sql       member management (needs 3)
-- #    5  workspace_invites.sql           invite links (needs 3 + 4)
-- #    6  project_history.sql             version history (needs 3)
-- #    7  master_assembly_schema.sql      optional; master-assembly feature (needs 3)
-- #    8  analytics_hardening.sql         optional; analytics dashboards (needs 1)
-- #
-- #  MINIMUM for accounts + invite links + CAD persistence:  steps 1-5.
-- #  Add 6 for history; 7/8 only if you use those features.
-- #
-- #  AFTER this runs, run migrate_cad_to_workspace.py to move the pre-accounts
-- #  CAD library into a workspace (its target tables are created here).
-- #
-- #  NOT included (ad-hoc diagnostics / one-offs — run manually only if needed):
-- #    analytics_verify.sql, analytics_reset_views.sql, analytics_baseline.sql,
-- #    analytics_minimal_views.sql, analytics_hard_cap.sql,
-- #    analytics_retention_cron.sql, diagnose_empty_live.sql,
-- #    diagnose_one_table.sql, when_does_data_start.sql, fix_linter_warnings.sql,
-- #    fix_missing_features.sql, set_labour_rate.sql
-- ############################################################################

-- ==============================================================================
-- ||                        1 · BASE ANALYTICS SCHEMA                         ||
-- ==============================================================================
-- Base analytics tables/views. Independent. Runs first so the isolation
-- script can retrofit workspace_id onto these tables.
-- source file: suspension/analytics_schema.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Usage Analytics & ROI schema (Supabase / Postgres)
--
--  PURPOSE
--  -------
--  Capture every meaningful interaction with KinematiK so that, months before a
--  board / faculty / sponsor review, there is REAL data behind claims like
--  "this tool saved the team N hours = $X". No vibes — an auditable event log
--  plus a set of views that compute:
--
--    * foot traffic            (sessions, daily/weekly active members)
--    * individual use          (per-member activity, per-feature breakdown)
--    * feature delivery time    (when a feature shipped vs first real use)
--    * render / pull latency    (how long compute + data fetches take)
--    * HOURS SAVED -> DOLLARS    (the headline board metric)
--    * comparison to alternatives (manual / spreadsheet / commercial tools)
--    * error rate per feature    (reliability, not just usage)
--    * retention                 (return users vs one-and-done)
--    * time-to-first-result      (how fast a new member gets value)
--    * adoption funnel           (open tab -> engage -> complete workflow)
--
--  DESIGN
--  ------
--  One wide append-only `analytics_events` table is the spine; everything else
--  is a VIEW computed from it, so instrumentation stays trivial (insert a row)
--  and new metrics never need a migration — just a new view. Two small config
--  tables hold the assumptions that turn raw counts into dollars, kept as DATA
--  so leads can tune them without code: `feature_baselines` (manual-hours per
--  feature) and `analytics_config` (labour rate, comparison tools).
--
--  All statements are idempotent so this file is a re-runnable migration.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  0. EXTENSIONS
-- ----------------------------------------------------------------------------
create extension if not exists "pgcrypto";   -- gen_random_uuid()


-- ----------------------------------------------------------------------------
--  0. RESET VIEWS FIRST — makes re-running this file always safe
-- ----------------------------------------------------------------------------
--  CREATE OR REPLACE VIEW cannot change an existing view's column set (Postgres
--  error 42P16: "cannot drop columns from view"). Whenever a view definition
--  below gains/loses/reorders a column relative to what's already deployed, the
--  replace fails. Dropping every metric view up front sidesteps that entirely —
--  they are recreated below with their current shape. Views hold no data, so
--  this is safe and idempotent; cascade clears dependents (e.g. v_roi_summary
--  depends on v_hours_saved_by_feature).
drop view if exists v_roi_summary                cascade;
drop view if exists v_hours_saved_by_feature     cascade;
drop view if exists v_comparison_to_alternatives cascade;
drop view if exists v_adoption_funnel            cascade;
drop view if exists v_time_to_first_result       cascade;
drop view if exists v_retention                  cascade;
drop view if exists v_error_rate                 cascade;
drop view if exists v_latency_by_feature         cascade;
drop view if exists v_feature_delivery           cascade;
drop view if exists v_feature_use                cascade;
drop view if exists v_individual_use             cascade;
drop view if exists v_active_members_weekly      cascade;
drop view if exists v_foot_traffic_daily         cascade;


-- ----------------------------------------------------------------------------
--  1. EVENTS — the append-only spine
-- ----------------------------------------------------------------------------
--  Every interaction is one row. `event_type` is a small controlled vocabulary;
--  `feature` is the tab/workflow id (matches _TAB_META keys, e.g. 'kinematics',
--  'registry', 'laptime'); `duration_ms` times renders/pulls; `success` and
--  `error_kind` give reliability; `value_payload` (jsonb) carries anything
--  extra without a schema change.
create table if not exists analytics_events (
    id            bigint generated always as identity primary key,
    occurred_at   timestamptz not null default now(),

    -- WHO (anonymous-by-default: a stable per-browser/session id, optional name)
    session_id    text not null,                 -- random uuid per browser SESSION (new each visit)
    visitor_id    text,                          -- durable id per BROWSER (survives visits, for retention)
    member        text,                          -- self-entered name/handle, optional
    subteam       text,                          -- 'suspension','aero',... or 'unknown'
    is_new_member boolean not null default false,-- first session ever for this id

    -- WHAT
    event_type    text not null
                    check (event_type in (
                      'session_start','tab_open','feature_engage',
                      'workflow_complete','render','data_pull','export',
                      'error','feature_released','first_result')),
    feature       text,                          -- tab/workflow id
    action        text,                          -- finer label, e.g. 'solve_kinematics'

    -- TIMING (for render/pull/compute latency)
    duration_ms   integer,                       -- how long it took, when relevant

    -- RELIABILITY
    success       boolean,                       -- null = n/a; true/false for ops
    error_kind    text,                          -- exception class / short reason

    -- ROI / EXTRA
    value_payload jsonb not null default '{}'::jsonb,
    app_version   text                           -- so metrics can be sliced by release
);

comment on table analytics_events is
  'Append-only interaction log. One row per event. All analytics views derive
   from this table; instrumentation only ever INSERTs.';

create index if not exists ae_time_idx     on analytics_events (occurred_at desc);
create index if not exists ae_session_idx  on analytics_events (session_id);
create index if not exists ae_feature_idx  on analytics_events (feature);
create index if not exists ae_type_idx     on analytics_events (event_type);
create index if not exists ae_member_idx   on analytics_events (member);
-- NOTE: we intentionally do NOT index (occurred_at::date). Casting timestamptz
-- to date is timezone-dependent and therefore not IMMUTABLE, which Postgres
-- rejects in an index expression (ERROR 42P17). The plain ae_time_idx above on
-- occurred_at already serves the day-bucketed range scans the views do, so no
-- functional date index is needed.


-- ----------------------------------------------------------------------------
--  2. FEATURE BASELINES — the manual-hours assumptions behind "hours saved"
-- ----------------------------------------------------------------------------
--  THIS IS THE KEY ROI INPUT. For each feature, how long the same task took the
--  OLD way (hand calc, spreadsheet, a commercial tool, or a meeting), and how
--  long it takes IN KinematiK. Hours saved per completion = manual - in_tool.
--  Kept as data + sourced/cited so the board number is defensible, not invented.
create table if not exists feature_baselines (
    feature             text primary key,        -- matches analytics_events.feature
    label               text not null,
    -- the slow path this feature replaces:
    manual_minutes      numeric not null,        -- minutes the OLD way took
    in_tool_minutes     numeric not null default 0, -- minutes it takes in KinematiK
    -- which alternative the manual estimate represents, for the comparison view:
    alternative         text,                    -- 'hand calc','Excel','OptimumK','ADAMS','meeting'
    alternative_cost_usd numeric default 0,      -- annual licence cost of the commercial tool, if any
    -- how the manual estimate was derived (timed a real task, lead estimate, etc):
    basis               text,                    -- 'timed','lead_estimate','literature'
    confidence          text default 'estimate'  -- 'measured' | 'estimate'
                          check (confidence in ('measured','estimate')),
    notes               text,
    updated_at          timestamptz not null default now()
);

comment on table feature_baselines is
  'Per-feature manual-vs-in-tool minute estimates that drive the hours-saved ROI.
   Edit these to tune the board number; each carries a basis + confidence so the
   estimate is defensible.';


-- ----------------------------------------------------------------------------
--  3. FEATURE RELEASES — when each feature shipped (for delivery-time metric)
-- ----------------------------------------------------------------------------
create table if not exists feature_releases (
    feature        text primary key,
    label          text,
    released_at    timestamptz not null,
    released_by    text,
    version        text,
    notes          text
);

comment on table feature_releases is
  'When each feature shipped — joined against first real use to measure
   time-to-adoption and delivery lead time.';


-- ----------------------------------------------------------------------------
--  4. GLOBAL CONFIG — labour rate + display assumptions (single row)
-- ----------------------------------------------------------------------------
create table if not exists analytics_config (
    id                  int primary key default 1 check (id = 1),
    labour_rate_usd_hr  numeric not null default 30,   -- $/hr to value saved time
    team_name           text default 'KinematiK',
    -- a representative commercial-tool annual cost the team would otherwise pay,
    -- shown in the board comparison as "avoided licence spend":
    avoided_licence_usd numeric default 0,
    updated_at          timestamptz not null default now()
);

insert into analytics_config (id) values (1) on conflict (id) do nothing;


-- ============================================================================
--  METRIC VIEWS — each answers one board question, read-only
-- ============================================================================

-- ----------------------------------------------------------------------------
--  A. FOOT TRAFFIC — sessions and active members over time
-- ----------------------------------------------------------------------------
create or replace view v_foot_traffic_daily as
select
    occurred_at::date                              as day,
    count(distinct session_id)                     as sessions,
    count(distinct member) filter (where member is not null) as named_members,
    count(*)                                        as events
from analytics_events
group by 1
order by 1;

-- weekly active members (the "are people actually using it" number)
create or replace view v_active_members_weekly as
select
    date_trunc('week', occurred_at)::date          as week,
    count(distinct coalesce(member, session_id))   as active_members,
    count(distinct session_id)                      as sessions
from analytics_events
group by 1
order by 1;


-- ----------------------------------------------------------------------------
--  B. INDIVIDUAL USE — per-member activity and per-feature breakdown
-- ----------------------------------------------------------------------------
create or replace view v_individual_use as
select
    coalesce(member, 'anon:' || left(session_id, 8)) as who,
    max(subteam)                                      as subteam,
    count(distinct session_id)                        as sessions,
    count(*) filter (where event_type = 'feature_engage') as feature_uses,
    count(*) filter (where event_type = 'workflow_complete') as workflows_completed,
    count(distinct feature)                           as distinct_features_used,
    min(occurred_at)                                  as first_seen,
    max(occurred_at)                                  as last_seen
from analytics_events
group by 1
order by feature_uses desc;

create or replace view v_feature_use as
select
    feature,
    count(*) filter (where event_type = 'tab_open')         as opens,
    count(*) filter (where event_type = 'feature_engage')   as engagements,
    count(*) filter (where event_type = 'workflow_complete') as completions,
    count(distinct coalesce(member, session_id))            as unique_users
from analytics_events
where feature is not null
group by 1
order by engagements desc;


-- ----------------------------------------------------------------------------
--  C. DELIVERY TIME — feature shipped vs first real use
-- ----------------------------------------------------------------------------
create or replace view v_feature_delivery as
select
    r.feature,
    r.label,
    r.released_at,
    min(e.occurred_at) filter (where e.event_type in ('feature_engage','workflow_complete'))
                                                    as first_real_use,
    extract(epoch from (
        min(e.occurred_at) filter (where e.event_type in ('feature_engage','workflow_complete'))
        - r.released_at) ) / 3600.0                 as hours_to_first_use
from feature_releases r
left join analytics_events e on e.feature = r.feature
group by r.feature, r.label, r.released_at
order by r.released_at desc;


-- ----------------------------------------------------------------------------
--  D. RENDER / PULL LATENCY — how long compute + data fetches take
-- ----------------------------------------------------------------------------
create or replace view v_latency_by_feature as
select
    feature,
    event_type,                                    -- 'render' or 'data_pull'
    count(*)                                       as n,
    round(avg(duration_ms))                        as avg_ms,
    percentile_cont(0.5) within group (order by duration_ms)  as p50_ms,
    percentile_cont(0.95) within group (order by duration_ms) as p95_ms,
    max(duration_ms)                               as max_ms
from analytics_events
where event_type in ('render','data_pull')
  and duration_ms is not null
group by 1, 2
order by avg_ms desc;


-- ----------------------------------------------------------------------------
--  E. ERROR RATE PER FEATURE — reliability
-- ----------------------------------------------------------------------------
create or replace view v_error_rate as
with ops as (
    select feature,
           count(*) filter (where success is not null)        as attempts,
           count(*) filter (where success is false
                              or event_type = 'error')         as failures
    from analytics_events
    where feature is not null
    group by feature
)
select
    feature,
    attempts,
    failures,
    case when attempts > 0
         then round(100.0 * failures / attempts, 2)
         else 0 end                                as error_rate_pct
from ops
order by error_rate_pct desc, attempts desc;


-- ----------------------------------------------------------------------------
--  F. RETENTION — return users vs one-and-done
--  (gained window_start/window_end columns; the drop block at the top of this
--   file clears the old shape so this recreation succeeds.)
-- ----------------------------------------------------------------------------
create or replace view v_retention as
with per_user as (
    -- identity for retention: prefer the entered name, else the durable browser
    -- visitor_id (survives across visits), and only fall back to the per-visit
    -- session_id when neither exists. This is what lets a returning ANONYMOUS
    -- user be recognised instead of looking like a brand-new one-timer each visit.
    select coalesce(member, visitor_id, session_id)  as uid,
           count(distinct occurred_at::date)        as active_days,
           count(distinct session_id)               as visits,
           min(occurred_at)::date                   as first_day,
           max(occurred_at)::date                   as last_day
    from analytics_events
    group by 1
)
select
    count(*)                                         as total_users,
    count(*) filter (where visits = 1)               as one_time_users,
    count(*) filter (where visits >= 2)              as returning_users,
    round(100.0 * count(*) filter (where visits >= 2)
          / nullif(count(*), 0), 1)                  as retention_pct,
    round(avg(visits), 2)                            as avg_visits_per_user,
    round(avg(active_days), 2)                       as avg_active_days,
    -- Live window bounds so the dashboard can show what period these numbers
    -- cover. window_start is the earliest event still retained (it moves
    -- forward as the 30-day/row cap trims old rows); window_end is the latest.
    min(first_day)                                   as window_start,
    max(last_day)                                    as window_end
from per_user;


-- ----------------------------------------------------------------------------
--  G. TIME-TO-FIRST-RESULT — how fast a new member gets something useful
-- ----------------------------------------------------------------------------
create or replace view v_time_to_first_result as
with starts as (
    select session_id,
           min(occurred_at) filter (where event_type = 'session_start') as t_start,
           min(occurred_at) filter (where event_type in
                ('first_result','workflow_complete'))                   as t_first_result
    from analytics_events
    group by session_id
)
select
    count(*) filter (where t_first_result is not null)         as sessions_with_result,
    round(avg(extract(epoch from (t_first_result - t_start)) / 60.0)::numeric, 2)
                                                               as avg_minutes_to_first_result,
    percentile_cont(0.5) within group (
        order by extract(epoch from (t_first_result - t_start)) / 60.0)
                                                               as median_minutes
from starts
where t_start is not null;


-- ----------------------------------------------------------------------------
--  H. ADOPTION FUNNEL — open tab -> engage -> complete, per feature
-- ----------------------------------------------------------------------------
create or replace view v_adoption_funnel as
select
    feature,
    count(distinct session_id) filter (where event_type = 'tab_open')          as opened,
    count(distinct session_id) filter (where event_type = 'feature_engage')    as engaged,
    count(distinct session_id) filter (where event_type = 'workflow_complete') as completed,
    round(100.0
          * count(distinct session_id) filter (where event_type = 'feature_engage')
          / nullif(count(distinct session_id) filter (where event_type = 'tab_open'), 0), 1)
                                                              as open_to_engage_pct,
    round(100.0
          * count(distinct session_id) filter (where event_type = 'workflow_complete')
          / nullif(count(distinct session_id) filter (where event_type = 'feature_engage'), 0), 1)
                                                              as engage_to_complete_pct
from analytics_events
where feature is not null
group by feature
order by completed desc;


-- ----------------------------------------------------------------------------
--  I. HOURS SAVED -> DOLLARS  (the headline board metric)
-- ----------------------------------------------------------------------------
--  For each feature: completions * (manual - in_tool) minutes = minutes saved.
--  Converted to hours, then to dollars at the configured labour rate. Carries
--  the baseline confidence so a "measured" saving is distinguishable from an
--  "estimate" — the board sees an honest number, not a best-case fantasy.
create or replace view v_hours_saved_by_feature as
select
    f.feature,
    f.label,
    f.alternative,
    f.confidence,
    count(*) filter (where e.event_type = 'workflow_complete') as completions,
    round((count(*) filter (where e.event_type = 'workflow_complete')
           * (f.manual_minutes - f.in_tool_minutes) / 60.0)::numeric, 1)
                                                               as hours_saved,
    round((count(*) filter (where e.event_type = 'workflow_complete')
           * (f.manual_minutes - f.in_tool_minutes) / 60.0
           * (select labour_rate_usd_hr from analytics_config where id = 1))::numeric, 0)
                                                               as dollars_saved
from feature_baselines f
left join analytics_events e on e.feature = f.feature
group by f.feature, f.label, f.alternative, f.confidence,
         f.manual_minutes, f.in_tool_minutes
order by hours_saved desc nulls last;

-- one-number roll-up for the headline slide
create or replace view v_roi_summary as
select
    round(sum(hours_saved)::numeric, 1)            as total_hours_saved,
    round(sum(dollars_saved)::numeric, 0)          as total_dollars_saved,
    (select labour_rate_usd_hr from analytics_config where id = 1) as labour_rate_usd_hr,
    (select avoided_licence_usd from analytics_config where id = 1) as avoided_licence_usd,
    round((sum(dollars_saved)
          + (select avoided_licence_usd from analytics_config where id = 1))::numeric, 0)
                                                    as total_value_usd
from v_hours_saved_by_feature;


-- ----------------------------------------------------------------------------
--  J. COMPARISON TO ALTERNATIVES — KinematiK vs the slow/expensive path
-- ----------------------------------------------------------------------------
create or replace view v_comparison_to_alternatives as
select
    f.alternative,
    count(distinct f.feature)                       as features_replacing,
    round(avg(f.manual_minutes)::numeric, 1)        as avg_manual_minutes,
    round(avg(f.in_tool_minutes)::numeric, 1)       as avg_in_tool_minutes,
    round(avg(f.manual_minutes - f.in_tool_minutes)::numeric, 1) as avg_minutes_saved_each,
    round((100.0 * (1 - avg(f.in_tool_minutes) / nullif(avg(f.manual_minutes), 0)))::numeric, 0)
                                                    as pct_faster,
    sum(f.alternative_cost_usd)                     as alternative_annual_cost_usd
from feature_baselines f
where f.alternative is not null
group by f.alternative
order by avg_minutes_saved_each desc;


-- ----------------------------------------------------------------------------
--  K. ROW-LEVEL SECURITY — open read for the dashboard; inserts via app key
-- ----------------------------------------------------------------------------
alter table analytics_events    enable row level security;
alter table feature_baselines   enable row level security;
alter table feature_releases    enable row level security;
alter table analytics_config    enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where policyname = 'ae_insert') then
        create policy ae_insert on analytics_events for insert with check (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'ae_read') then
        create policy ae_read on analytics_events for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'fb_read') then
        create policy fb_read on feature_baselines for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'fr_read') then
        create policy fr_read on feature_releases for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'cfg_read') then
        create policy cfg_read on analytics_config for select using (true);
    end if;
end $$;


-- ============================================================================
--  SEED — baselines + releases for the tabs that exist today
--  These numbers are STARTING estimates (confidence='estimate'); replace with
--  timed measurements as you collect them. Sources noted in `basis`/`notes`.
-- ============================================================================
insert into feature_baselines (feature, label, manual_minutes, in_tool_minutes, alternative, alternative_cost_usd, basis, confidence, notes) values
  ('kinematics','Kinematics solve',         180, 5,  'OptimumK',   0,    'lead_estimate','estimate','Hardpoint sweep + camber/bump-steer read that OptimumK or a spreadsheet would take an afternoon.'),
  ('laptime','Lap-time sim',                240, 8,  'spreadsheet',0,    'lead_estimate','estimate','Quasi-steady lap by hand / Excel vs one solve.'),
  ('ggv','GGV envelope',                    120, 3,  'spreadsheet',0,    'lead_estimate','estimate',null),
  ('brakes','Brake bias + bolt FoS',        150, 10, 'hand calc',  0,    'lead_estimate','estimate','VDI 2230 bolt calc + bias by hand.'),
  ('registry','Find current released part', 25,  1,  'Drive hunt', 0,    'timed','estimate','Hunting a Drive folder + asking in Discord for the current file.'),
  ('integration','Cross-subsystem sync',    90,  5,  'meeting',    0,    'lead_estimate','estimate','The "whose number is current" reconciliation meeting.'),
  ('dfmea','DFMEA update',                  120, 15, 'Excel',      0,    'lead_estimate','estimate',null),
  ('cost','Cost & BOM',                     180, 20, 'Excel',      0,    'lead_estimate','estimate',null),
  ('accum','Accumulator sizing + rules',    150, 10, 'spreadsheet',0,    'lead_estimate','estimate',null)
on conflict (feature) do nothing;

-- Example release rows (set to your real ship dates).
insert into feature_releases (feature, label, released_at, version) values
  ('registry','Registry',   now() - interval '7 days',  'v0.9'),
  ('integration','Integration', now() - interval '90 days', 'v0.5')
on conflict (feature) do nothing;

-- ==============================================================================
-- ||                       2 · BASE MYTH-ENGINE SCHEMA                        ||
-- ==============================================================================
-- Base myth-engine tables. Independent. Retrofitted with workspace_id by the
-- isolation script, so it must exist first.
-- source file: suspension/myth_schema.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Myth-Buster entity engine schema (Supabase / Postgres)
--  Created for the KinematiK Formula SAE toolkit.
--
--  WHAT THIS IS
--  ------------
--  A data-driven backing store for the cross-discipline Myth-Buster. Instead of
--  hard-coding "does more downforce increase speed?" as a hand-written keyword
--  rule in Python, discipline leads declare three things as DATA:
--
--    * ENTITIES   — the physical quantities a claim can be about
--                   (Downforce, Drag, Speed, Mass, Grip …), each with the
--                   surface words people actually type ("aero", "df", "grip").
--    * FORMULAS   — named, deterministic expressions evaluated by a safe math
--                   sandbox (no `eval`, no LLM): "0.5 * rho * v**2 * ClA".
--    * RELATIONS  — directed edges linking a SOURCE entity to a TARGET entity,
--                   carrying the qualitative sign (increases / decreases /
--                   depends), an optional FORMULA that quantifies it, and the
--                   verdict + explanation the engine returns.
--
--  The Python engine (suspension/myth_entity_engine.py) reads these tables,
--  resolves the entities in a free-text claim, looks for a relationship between
--  them, evaluates the formula against live registry values, and returns a
--  verdict + a transparent CONFIDENCE SCORE. Leads add rows; nobody edits the
--  engine.
--
--  CONVENTIONS
--  -----------
--  * snake_case table + column names (Postgres default-folding friendly).
--  * Every table carries `discipline` so a lead owns their rows and RLS can be
--    scoped per-channel later.
--  * `slug` columns are stable machine keys; `label` columns are human display.
--  * Timestamps are `timestamptz default now()`.
--  All statements are idempotent (IF NOT EXISTS / CREATE OR REPLACE) so this
--  file can be re-run as a migration without error.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  1. ENTITIES — the quantities claims are about
-- ----------------------------------------------------------------------------
create table if not exists myth_entities (
    id            uuid primary key default gen_random_uuid(),
    slug          text unique not null,          -- 'downforce', 'speed' (machine key)
    label         text not null,                 -- 'Downforce' (display)
    discipline    text not null default 'shared',-- owning channel, or 'shared'
    symbol        text,                          -- 'F_z', 'v' — used in formulas
    canonical_unit text,                         -- 'N', 'm/s', 'kg' (informational)
    -- the surface forms a parser should match, lower-cased. Includes synonyms,
    -- abbreviations and common misspellings. Matched as whole-word / phrase.
    aliases       text[] not null default '{}',
    -- optional key into the live registry / integration ledger so the engine can
    -- pull a *verified* numeric value for this entity (e.g. 'aero.ClA').
    registry_key  text,
    description   text,
    created_by    text,
    created_at    timestamptz not null default now()
);

comment on table myth_entities is
    'Physical quantities the Myth-Buster reasons about. aliases[] are the surface
     words a free-text parser matches; registry_key links to a verified live value.';

create index if not exists myth_entities_discipline_idx on myth_entities (discipline);
-- GIN index so `aliases && array['aero']` (overlap) and contains queries are fast.
create index if not exists myth_entities_aliases_gin on myth_entities using gin (aliases);


-- ----------------------------------------------------------------------------
--  2. FORMULAS — named deterministic expressions
-- ----------------------------------------------------------------------------
--  `expression` is evaluated by the Python safe-eval sandbox (asteval/AST),
--  NOT by Postgres and NOT by eval(). `inputs` lists the variable names the
--  expression needs; the engine binds each from (a) numbers parsed out of the
--  claim, (b) a verified registry value, or (c) the formula-level `defaults`.
create table if not exists myth_formulas (
    id            uuid primary key default gen_random_uuid(),
    slug          text unique not null,          -- 'aero_force', 'tractive_limit'
    label         text not null,
    discipline    text not null default 'shared',
    expression    text not null,                 -- '0.5 * rho * v**2 * ClA'
    inputs        text[] not null default '{}',  -- ['rho','v','ClA']
    -- baseline values used when neither the claim nor the registry supplies an
    -- input. Using a default DEMOTES confidence (see relationship resolution).
    defaults      jsonb not null default '{}'::jsonb,  -- {"rho": 1.225}
    output_unit   text,
    -- 'physics' (a conservation/scaling law, high trust), 'empirical' (fit to
    -- data), or 'heuristic' (rule of thumb). Feeds the confidence score.
    basis         text not null default 'physics'
                    check (basis in ('physics','empirical','heuristic')),
    reference     text,                          -- citation / derivation note
    created_by    text,
    created_at    timestamptz not null default now()
);

comment on table myth_formulas is
    'Named deterministic expressions evaluated by the Python AST sandbox. `inputs`
     are bound from claim numbers, verified registry values, or `defaults`.';

create index if not exists myth_formulas_discipline_idx on myth_formulas (discipline);


-- ----------------------------------------------------------------------------
--  3. RELATIONSHIPS — directed edges between two entities
-- ----------------------------------------------------------------------------
--  This is the mapping table the brief asked for: it links entities to each
--  other AND (optionally) to a formula. A claim "does more <source> increase
--  <target>?" is answered by finding the row where source_entity_id ->
--  target_entity_id, reading its `effect` sign, optionally evaluating its
--  formula, and returning `verdict` + `explanation`.
create table if not exists myth_relationships (
    id              uuid primary key default gen_random_uuid(),
    slug            text unique not null,
    discipline      text not null default 'shared',
    source_entity_id uuid not null references myth_entities (id) on delete cascade,
    target_entity_id uuid not null references myth_entities (id) on delete cascade,
    -- qualitative sign of source -> target, the spine of the heuristic:
    --   'increases'  more source -> more target
    --   'decreases'  more source -> less target
    --   'depends'    sign is conditional (trade-off); engine returns DEPENDS
    --   'none'       asserted independence
    effect          text not null default 'depends'
                      check (effect in ('increases','decreases','depends','none')),
    -- whether the edge is symmetric (A<->B) or one-directional (A->B only).
    bidirectional   boolean not null default false,
    -- the verdict the engine returns when THIS edge resolves a claim. Matches
    -- the engine's Verdict vocabulary so the UI is unchanged.
    verdict         text not null default 'depends'
                      check (verdict in ('true','myth','depends','unknown')),
    -- optional formula that quantifies the edge. If present and evaluable, the
    -- engine reports the computed number and raises confidence.
    formula_id      uuid references myth_formulas (id) on delete set null,
    -- human explanation; may contain {placeholders} the engine fills from the
    -- formula result, e.g. "4x the speed gives {ratio:.0f}x the downforce".
    explanation     text not null,
    -- short provenance string shown under the verdict.
    provenance      text,
    -- author-declared trust in the EDGE ITSELF (independent of the formula
    -- basis): 'verified' (checked against data/sim), 'modeled', 'judgement'.
    confidence_basis text not null default 'modeled'
                      check (confidence_basis in ('verified','modeled','judgement')),
    priority        int not null default 100,    -- lower wins on ties
    enabled         boolean not null default true,
    created_by      text,
    created_at      timestamptz not null default now(),
    -- a source/target pair is unique per direction so leads don't create
    -- duplicate contradictory edges by accident.
    unique (source_entity_id, target_entity_id, discipline)
);

comment on table myth_relationships is
    'Directed entity->entity edges. effect gives the qualitative sign; an optional
     formula_id quantifies it; verdict/explanation are what the engine returns.';

create index if not exists myth_rel_source_idx on myth_relationships (source_entity_id);
create index if not exists myth_rel_target_idx on myth_relationships (target_entity_id);
create index if not exists myth_rel_discipline_idx on myth_relationships (discipline);


-- ----------------------------------------------------------------------------
--  4. GENERAL PHYSICS FALLBACKS — discipline-agnostic laws
-- ----------------------------------------------------------------------------
--  When no specific entity->entity relationship exists, the engine consults
--  these broad laws keyed by entity *kind* (e.g. any 'force' vs any 'speed').
--  This is the "fall back to a general physics law" path in the brief, and it
--  resolves at LOWER confidence than a specific relationship.
create table if not exists myth_fallback_laws (
    id            uuid primary key default gen_random_uuid(),
    slug          text unique not null,
    label         text not null,
    -- a coarse predicate: which entity kinds this law applies to. The engine
    -- tags each entity with a `kind` (force/speed/mass/energy/thermal/...) via
    -- myth_entities.canonical_unit or an explicit kind column if you add one.
    source_kind   text,
    target_kind   text,
    effect        text not null default 'depends'
                    check (effect in ('increases','decreases','depends','none')),
    verdict       text not null default 'depends'
                    check (verdict in ('true','myth','depends','unknown')),
    formula_id    uuid references myth_formulas (id) on delete set null,
    explanation   text not null,
    created_at    timestamptz not null default now()
);

comment on table myth_fallback_laws is
    'Discipline-agnostic laws consulted when no specific relationship matches.
     Resolves at lower confidence than a specific myth_relationships edge.';


-- ----------------------------------------------------------------------------
--  5. AUDIT LOG (optional but recommended) — every check, for transparency
-- ----------------------------------------------------------------------------
--  Records each resolved claim so the team can see what was asked, what edge
--  answered it, and at what confidence — turning the "AI-like" box into an
--  auditable, deterministic log instead of a black box.
create table if not exists myth_check_log (
    id              uuid primary key default gen_random_uuid(),
    raw_input       text not null,
    matched_source  text,
    matched_target  text,
    relationship_slug text,
    verdict         text,
    confidence      numeric(4,3),                -- 0.000 .. 1.000
    confidence_tier text,                        -- 'formula' | 'registry' | 'baseline' | 'fallback'
    used_registry   boolean default false,
    checked_at      timestamptz not null default now()
);

create index if not exists myth_check_log_time_idx on myth_check_log (checked_at desc);


-- ----------------------------------------------------------------------------
--  6. CONVENIENCE VIEW — relationships joined to their entity + formula names
-- ----------------------------------------------------------------------------
--  The Python loader selects from this so it gets human-readable slugs in one
--  round-trip instead of resolving foreign keys client-side.
create or replace view myth_relationship_resolved as
select
    r.id,
    r.slug,
    r.discipline,
    r.effect,
    r.bidirectional,
    r.verdict,
    r.explanation,
    r.provenance,
    r.confidence_basis,
    r.priority,
    r.enabled,
    s.slug   as source_slug,
    s.label  as source_label,
    s.aliases as source_aliases,
    s.registry_key as source_registry_key,
    t.slug   as target_slug,
    t.label  as target_label,
    t.aliases as target_aliases,
    t.registry_key as target_registry_key,
    f.slug   as formula_slug,
    f.expression as formula_expression,
    f.inputs as formula_inputs,
    f.defaults as formula_defaults,
    f.basis  as formula_basis
from myth_relationships r
join myth_entities s on s.id = r.source_entity_id
join myth_entities t on t.id = r.target_entity_id
left join myth_formulas f on f.id = r.formula_id
where r.enabled;


-- ----------------------------------------------------------------------------
--  7. ROW-LEVEL SECURITY (optional, recommended for a shared team DB)
-- ----------------------------------------------------------------------------
--  Read is open to the team; writes can be gated per discipline once you wire
--  auth. Left permissive here so local dev works; tighten in production.
alter table myth_entities       enable row level security;
alter table myth_formulas       enable row level security;
alter table myth_relationships  enable row level security;
alter table myth_fallback_laws  enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where policyname = 'myth_entities_read') then
        create policy myth_entities_read on myth_entities for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'myth_formulas_read') then
        create policy myth_formulas_read on myth_formulas for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'myth_relationships_read') then
        create policy myth_relationships_read on myth_relationships for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'myth_fallback_read') then
        create policy myth_fallback_read on myth_fallback_laws for select using (true);
    end if;
end $$;


-- ============================================================================
--  8. SEED DATA — the "does more downforce increase speed?" example, as DATA
-- ============================================================================
--  These rows reproduce the existing hard-coded aero rules with zero Python.
insert into myth_entities (slug, label, discipline, symbol, canonical_unit, aliases, registry_key)
values
  ('downforce','Downforce','aerodynamics','F_z','N',
     array['downforce','down force','df','aero load','vertical load','grip from aero'], 'aero.downforce_n'),
  ('drag','Drag','aerodynamics','F_x','N',
     array['drag','aero drag','cda','c_d a'], 'aero.cda'),
  ('speed','Speed','shared','v','m/s',
     array['speed','velocity','top speed','straight line speed','straight-line speed','how fast'], null),
  ('cornering','Cornering grip','suspension','F_y','N',
     array['cornering','corner speed','lateral grip','cornering grip','grip in corners'], null),
  ('laptime','Lap time','shared','t','s',
     array['lap time','laptime','faster lap','quicker lap','lower lap'], null)
on conflict (slug) do nothing;

insert into myth_formulas (slug, label, discipline, expression, inputs, defaults, output_unit, basis, reference)
values
  ('aero_force','Aerodynamic force','aerodynamics',
     '0.5 * rho * v**2 * CA', array['rho','v','CA'],
     '{"rho": 1.225, "CA": 1.0}'::jsonb, 'N', 'physics',
     'F = 1/2 rho V^2 C A — standard aero force; force scales with V^2.'),
  ('speed_force_ratio','Force ratio from speed ratio','shared',
     '(v2 / v1)**2', array['v1','v2'], '{}'::jsonb, 'ratio', 'physics',
     'Aero force ratio for a speed change: (V2/V1)^2.')
on conflict (slug) do nothing;

-- Downforce -> Speed: the classic trade. More downforce helps cornering but its
-- drag costs straight-line speed: the answer is DEPENDS, not a flat yes.
insert into myth_relationships
  (slug, discipline, source_entity_id, target_entity_id, effect, verdict,
   formula_id, explanation, provenance, confidence_basis, priority)
select
  'aero.downforce_vs_speed','aerodynamics',
  (select id from myth_entities where slug='downforce'),
  (select id from myth_entities where slug='speed'),
  'depends','depends', null,
  'More downforce raises cornering grip but its drag lowers straight-line speed '
  'and uses more energy — on an EV with a fixed pack that is a real cost. It is a '
  'lap-time trade, track-dependent: downforce wins on tight autocross and can LOSE '
  'on a fast track. Resolve it in the lap sim with your real aero map.',
  'F=1/2 rho V^2 C A: downforce and drag both scale with V^2',
  'modeled', 20
on conflict (source_entity_id, target_entity_id, discipline) do nothing;

-- Downforce -> Cornering: unambiguous increase (the part the myth gets right).
insert into myth_relationships
  (slug, discipline, source_entity_id, target_entity_id, effect, verdict,
   formula_id, explanation, provenance, confidence_basis, priority)
select
  'aero.downforce_vs_cornering','aerodynamics',
  (select id from myth_entities where slug='downforce'),
  (select id from myth_entities where slug='cornering'),
  'increases','true',
  (select id from myth_formulas where slug='aero_force'),
  'More downforce increases the vertical load on the tyres, which raises the '
  'lateral force they can make — so corner speed goes up. The gain tapers with '
  'tyre load sensitivity and costs drag, but the sign is positive.',
  'downforce adds tyre normal load -> more lateral grip',
  'verified', 15
on conflict (source_entity_id, target_entity_id, discipline) do nothing;

-- General fallback: any aero force vs any speed scales with V^2 (the scaling-law
-- answer when no specific edge exists).
insert into myth_fallback_laws (slug, label, source_kind, target_kind, effect, verdict, formula_id, explanation)
select 'aero_scaling_v2','Aero force scales with V^2','force','speed','increases','depends',
  (select id from myth_formulas where slug='speed_force_ratio'),
  'Aerodynamic force scales with the SQUARE of speed (F = 1/2 rho V^2 C A): double '
  'the speed gives 4x the force, both downforce and drag. Evaluate aero at the '
  'speeds the track actually spends time at, from the lap sim.'
on conflict (slug) do nothing;

-- ==============================================================================
-- ||                 3 · WORKSPACE ISOLATION (TENANCY SPINE)                  ||
-- ==============================================================================
-- Creates workspaces, workspace_members, kinematik_workspace_projects, the
-- is_workspace_member()/workspace_role() helpers, and RLS on every tenant
-- table. REQUIRED before accounts, invite links, and the CAD-library
-- migration. Header says: run AFTER analytics_schema.sql / myth_schema.sql.
-- source file: suspension/workspace_isolation.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Workspace isolation migration (multi-tenant hardening)
--  Idempotent: safe to re-run. Run AFTER analytics_schema.sql / myth_schema.sql.
--
--  TENANCY MODEL
--    workspaces            one row per team / external EV startup
--    workspace_members     (workspace_id, user_id, role) — the ONLY grant path
--    kinematik_workspace_projects   tenant-scoped project ledger,
--                                   PK (workspace_id, id)
--    + workspace_id columns retrofitted onto myth/analytics tables.
--
--  ISOLATION RULES (enforced here, mirrored in suspension/workspace.py):
--    * RLS ENABLED + FORCED on every tenant table (FORCE = even table owner
--      obeys policies; only service_role, used exclusively server-side, bypasses).
--    * Every policy routes through is_workspace_member(); there is no policy
--      that grants by workspace name, project id, or "true".
--    * INSERT/UPDATE use WITH CHECK so a member of A can never write a row
--      stamped B — cross-workspace writes fail at the database, not the app.
--    * anon has NO direct table privileges; authenticated has table privileges
--      but every row still passes RLS.
-- ============================================================================

create extension if not exists pgcrypto;

-- ----------------------------------------------------------------------------
--  1. Tenancy spine
-- ----------------------------------------------------------------------------
create table if not exists workspaces (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    kind        text not null default 'team'
                check (kind in ('team', 'ev_startup', 'sandbox')),
    created_by  uuid,                       -- auth.users.id of the creator
    created_at  timestamptz not null default now()
);

create table if not exists workspace_members (
    workspace_id uuid not null references workspaces(id) on delete cascade,
    user_id      uuid not null,             -- auth.users.id
    role         text not null default 'member'
                 check (role in ('owner', 'lead', 'member', 'viewer')),
    added_at     timestamptz not null default now(),
    primary key (workspace_id, user_id)
);
create index if not exists idx_wm_user on workspace_members (user_id);

-- SECURITY DEFINER so policies on member-gated tables can consult membership
-- without recursing into workspace_members' own RLS. STABLE: one snapshot/stmt.
create or replace function is_workspace_member(ws uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select exists (select 1 from workspace_members m
                   where m.workspace_id = ws and m.user_id = auth.uid());
$$;

create or replace function workspace_role(ws uuid)
returns text language sql stable security definer set search_path = public as $$
    select m.role from workspace_members m
    where m.workspace_id = ws and m.user_id = auth.uid();
$$;

-- ----------------------------------------------------------------------------
--  2. Tenant-scoped project ledger (accounts/parameters/configurations/vehicle
--     ledgers all live inside the jsonb `data` document, one doc per project)
-- ----------------------------------------------------------------------------
create table if not exists kinematik_workspace_projects (
    workspace_id uuid not null references workspaces(id) on delete cascade,
    id           text not null default 'default',
    data         jsonb not null default '{}'::jsonb,
    updated_at   timestamptz not null default now(),
    primary key (workspace_id, id)
);
create index if not exists idx_kwp_ws on kinematik_workspace_projects (workspace_id);

-- One-time migration of the legacy single-tenant table into a legacy workspace.
do $$
declare legacy_ws uuid;
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'kinematik_project')
       and not exists (select 1 from workspaces where name = '__legacy__') then
        insert into workspaces (name, kind) values ('__legacy__', 'team')
        returning id into legacy_ws;
        insert into kinematik_workspace_projects (workspace_id, id, data)
        select legacy_ws, p.id, p.data from kinematik_project p
        on conflict (workspace_id, id) do nothing;
    end if;
end $$;

-- ----------------------------------------------------------------------------
--  3. Retrofit workspace_id onto existing shared tables (myth KB, analytics).
--     Conditional: each block is a no-op if the table isn't deployed.
-- ----------------------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array['myth_entities','myth_edges','feature_events'] loop
        if exists (select 1 from information_schema.tables where table_name = t) then
            execute format(
                'alter table %I add column if not exists workspace_id uuid
                 references workspaces(id) on delete cascade', t);
            execute format(
                'create index if not exists idx_%s_ws on %I (workspace_id)', t, t);
        end if;
    end loop;
end $$;

-- Per-workspace uniqueness for myth entities (global unique name would leak
-- existence across tenants and block two teams from naming the same part).
do $$
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'myth_entities') then
        begin
            alter table myth_entities drop constraint if exists myth_entities_name_key;
        exception when others then null;
        end;
        create unique index if not exists uq_myth_entities_ws_name
            on myth_entities (workspace_id, lower(name));
    end if;
end $$;

-- ----------------------------------------------------------------------------
--  4. Row-Level Security — the tenant wall
-- ----------------------------------------------------------------------------
alter table workspaces                    enable row level security;
alter table workspaces                    force  row level security;
alter table workspace_members             enable row level security;
alter table workspace_members             force  row level security;
alter table kinematik_workspace_projects  enable row level security;
alter table kinematik_workspace_projects  force  row level security;

-- workspaces: visible only to members; creatable by any authenticated user
-- (creator immediately self-enrolls as owner via the trigger below).
drop policy if exists ws_select on workspaces;
create policy ws_select on workspaces for select
    using (is_workspace_member(id));
drop policy if exists ws_insert on workspaces;
create policy ws_insert on workspaces for insert to authenticated
    with check (created_by = auth.uid());
drop policy if exists ws_update on workspaces;
create policy ws_update on workspaces for update
    using (workspace_role(id) = 'owner') with check (workspace_role(id) = 'owner');
drop policy if exists ws_delete on workspaces;
create policy ws_delete on workspaces for delete
    using (workspace_role(id) = 'owner');

create or replace function _ws_owner_bootstrap() returns trigger
language plpgsql security definer set search_path = public as $$
begin
    insert into workspace_members (workspace_id, user_id, role)
    values (new.id, new.created_by, 'owner')
    on conflict do nothing;
    return new;
end $$;
drop trigger if exists trg_ws_owner_bootstrap on workspaces;
create trigger trg_ws_owner_bootstrap after insert on workspaces
    for each row execute function _ws_owner_bootstrap();

-- workspace_members: members see their workspace's roster; only owner/lead mutate.
drop policy if exists wm_select on workspace_members;
create policy wm_select on workspace_members for select
    using (is_workspace_member(workspace_id));
drop policy if exists wm_insert on workspace_members;
create policy wm_insert on workspace_members for insert
    with check (workspace_role(workspace_id) in ('owner','lead'));
drop policy if exists wm_update on workspace_members;
create policy wm_update on workspace_members for update
    using (workspace_role(workspace_id) = 'owner')
    with check (workspace_role(workspace_id) = 'owner');
drop policy if exists wm_delete on workspace_members;
create policy wm_delete on workspace_members for delete
    using (workspace_role(workspace_id) = 'owner' or user_id = auth.uid());

-- project ledger: member-read, writer-roles write, workspace stamped & checked.
drop policy if exists kwp_select on kinematik_workspace_projects;
create policy kwp_select on kinematik_workspace_projects for select
    using (is_workspace_member(workspace_id));
drop policy if exists kwp_write on kinematik_workspace_projects;
create policy kwp_write on kinematik_workspace_projects for insert
    with check (workspace_role(workspace_id) in ('owner','lead','member'));
drop policy if exists kwp_update on kinematik_workspace_projects;
create policy kwp_update on kinematik_workspace_projects for update
    using (workspace_role(workspace_id) in ('owner','lead','member'))
    with check (workspace_role(workspace_id) in ('owner','lead','member'));
drop policy if exists kwp_delete on kinematik_workspace_projects;
create policy kwp_delete on kinematik_workspace_projects for delete
    using (workspace_role(workspace_id) in ('owner','lead'));

-- Retrofitted tables get the same member gate (conditional on deployment).
do $$
declare t text;
begin
    foreach t in array array['myth_entities','myth_edges','feature_events'] loop
        if exists (select 1 from information_schema.tables where table_name = t) then
            execute format('alter table %I enable row level security', t);
            execute format('alter table %I force  row level security', t);
            execute format('drop policy if exists %s_ws_select on %I', t, t);
            execute format(
                'create policy %s_ws_select on %I for select
                 using (is_workspace_member(workspace_id))', t, t);
            execute format('drop policy if exists %s_ws_write on %I', t, t);
            execute format(
                'create policy %s_ws_write on %I for insert
                 with check (is_workspace_member(workspace_id)
                             and workspace_role(workspace_id) <> ''viewer'')', t, t);
            execute format('drop policy if exists %s_ws_update on %I', t, t);
            execute format(
                'create policy %s_ws_update on %I for update
                 using (is_workspace_member(workspace_id))
                 with check (is_workspace_member(workspace_id)
                             and workspace_role(workspace_id) <> ''viewer'')', t, t);
        end if;
    end loop;
end $$;

-- ----------------------------------------------------------------------------
--  5. Privilege hygiene: nothing for anon; authenticated goes through RLS.
-- ----------------------------------------------------------------------------
revoke all on workspaces, workspace_members, kinematik_workspace_projects from anon;
grant select, insert, update, delete
    on workspaces, workspace_members, kinematik_workspace_projects to authenticated;
grant execute on function is_workspace_member(uuid), workspace_role(uuid)
    to authenticated;
revoke execute on function is_workspace_member(uuid), workspace_role(uuid) from anon;

-- Legacy single-tenant table: freeze it read-none for API roles once migrated.
do $$
begin
    if exists (select 1 from information_schema.tables
               where table_name = 'kinematik_project') then
        revoke all on kinematik_project from anon, authenticated;
        alter table kinematik_project enable row level security;
        alter table kinematik_project force  row level security;  -- no policies ⇒ no access
    end if;
end $$;

-- ==============================================================================
-- ||                        4 · MEMBER-MANAGEMENT RPCs                        ||
-- ==============================================================================
-- add/list/change-role/remove-by-email RPCs (incl. _require_member_admin).
-- Run AFTER workspace_isolation.sql.
-- source file: suspension/workspace_members_rpc.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Member-management RPCs (add/list/change-role/remove by email)
--  Idempotent. Run AFTER workspace_isolation.sql.
--
--  WHY RPCs: workspace_members.user_id is an auth.users.id (UUID), and RLS
--  correctly forbids the anon/user client from reading auth.users. So the app
--  cannot translate an email -> user id on the client. These SECURITY DEFINER
--  functions do that translation server-side, but every one of them RE-CHECKS
--  the caller's role with the same rules RLS enforces — the definer privilege
--  is used ONLY to read auth.users and to write the membership row, never to
--  bypass the permission model:
--
--     add / change-role   -> caller must be 'owner' or 'lead'
--     change to/from any role, remove others -> 'owner' or 'lead'
--     a user may always remove themselves (leave)
--
--  Roles handed out through the app are 'lead' and 'member'. 'owner' is the
--  creator (set by the workspace trigger) and is never assigned here; 'viewer'
--  exists in the schema but isn't offered by this UI.
-- ============================================================================

-- Guard: only 'owner'/'lead' may administer members. Raises otherwise.
create or replace function _require_member_admin(ws uuid)
returns void language plpgsql stable security definer set search_path = public as $$
begin
    if public.workspace_role(ws) not in ('owner', 'lead') then
        raise exception 'permission denied: only owner or lead may manage members'
            using errcode = '42501';
    end if;
end $$;

-- ----------------------------------------------------------------------------
--  List members with their emails (members-only; emails come from auth.users).
--  Returns user_id, email, role, added_at for the roster UI.
-- ----------------------------------------------------------------------------
create or replace function list_workspace_members(ws uuid)
returns table (user_id uuid, email text, role text, added_at timestamptz)
language plpgsql stable security definer set search_path = public as $$
begin
    if not public.is_workspace_member(ws) then
        raise exception 'permission denied: not a member of this workspace'
            using errcode = '42501';
    end if;
    return query
        select m.user_id, u.email::text, m.role, m.added_at
        from workspace_members m
        join auth.users u on u.id = m.user_id
        where m.workspace_id = ws
        order by
            case m.role when 'owner' then 0 when 'lead' then 1
                        when 'member' then 2 else 3 end,
            u.email;
end $$;

-- ----------------------------------------------------------------------------
--  Add a member by email. Caller must be owner/lead. Role limited to
--  lead/member/viewer (never 'owner' — ownership isn't transferable this way).
--  Idempotent: if already a member, updates their role instead of erroring.
-- ----------------------------------------------------------------------------
create or replace function add_workspace_member(ws uuid, member_email text,
                                                member_role text default 'member')
returns uuid language plpgsql security definer set search_path = public as $$
declare target uuid;
begin
    perform public._require_member_admin(ws);

    if member_role not in ('lead', 'member', 'viewer') then
        raise exception 'invalid role %, expected lead/member/viewer', member_role
            using errcode = '22023';
    end if;

    select id into target from auth.users
        where lower(email) = lower(trim(member_email)) limit 1;
    if target is null then
        raise exception 'no user with email % — they must create an account first',
            member_email using errcode = 'P0002';
    end if;

    insert into workspace_members (workspace_id, user_id, role)
    values (ws, target, member_role)
    on conflict (workspace_id, user_id) do update set role = excluded.role;

    return target;
end $$;

-- ----------------------------------------------------------------------------
--  Change an existing member's role. Caller must be owner/lead. Cannot target
--  an owner and cannot promote to owner (ownership is not reassigned here).
-- ----------------------------------------------------------------------------
create or replace function set_workspace_member_role(ws uuid, target_user uuid,
                                                     new_role text)
returns void language plpgsql security definer set search_path = public as $$
declare cur text;
begin
    perform public._require_member_admin(ws);

    if new_role not in ('lead', 'member', 'viewer') then
        raise exception 'invalid role %, expected lead/member/viewer', new_role
            using errcode = '22023';
    end if;

    select role into cur from workspace_members
        where workspace_id = ws and user_id = target_user;
    if cur is null then
        raise exception 'that user is not a member of this workspace'
            using errcode = 'P0002';
    end if;
    if cur = 'owner' then
        raise exception 'cannot change the owner''s role' using errcode = '42501';
    end if;

    update workspace_members set role = new_role
        where workspace_id = ws and user_id = target_user;
end $$;

-- ----------------------------------------------------------------------------
--  Remove a member. Owner/lead may remove anyone except the owner; any user may
--  remove themselves (leave). The owner cannot be removed via this function.
-- ----------------------------------------------------------------------------
create or replace function remove_workspace_member(ws uuid, target_user uuid)
returns void language plpgsql security definer set search_path = public as $$
declare cur text;
begin
    select role into cur from workspace_members
        where workspace_id = ws and user_id = target_user;
    if cur is null then
        return;  -- already gone; idempotent no-op
    end if;
    if cur = 'owner' then
        raise exception 'the owner cannot be removed' using errcode = '42501';
    end if;

    -- Either an admin, or the user leaving on their own.
    if not (public.workspace_role(ws) in ('owner', 'lead')
            or target_user = auth.uid()) then
        raise exception 'permission denied: cannot remove that member'
            using errcode = '42501';
    end if;

    delete from workspace_members
        where workspace_id = ws and user_id = target_user;
end $$;

-- ----------------------------------------------------------------------------
--  Privileges: authenticated may call; anon may not. (RLS/role checks inside
--  each function are the real gate; this just keeps anon off the RPC surface.)
-- ----------------------------------------------------------------------------
revoke all on function
    list_workspace_members(uuid),
    add_workspace_member(uuid, text, text),
    set_workspace_member_role(uuid, uuid, text),
    remove_workspace_member(uuid, uuid),
    _require_member_admin(uuid)
    from anon;
grant execute on function
    list_workspace_members(uuid),
    add_workspace_member(uuid, text, text),
    set_workspace_member_role(uuid, uuid, text),
    remove_workspace_member(uuid, uuid)
    to authenticated;

-- ==============================================================================
-- ||                        5 · WORKSPACE INVITE LINKS                        ||
-- ==============================================================================
-- Self-serve invite-link RPCs. Calls _require_member_admin() from step 4, so
-- run AFTER workspace_isolation.sql AND workspace_members_rpc.sql.
-- source file: suspension/workspace_invites.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — self-serve team onboarding: workspace invite links
--  Run once in the Supabase SQL editor AFTER workspace_isolation.sql and
--  workspace_members_rpc.sql. Idempotent — safe to re-run.
--
--  WHY: growth arrives as individuals (Discord), but the product's value
--  activates per-team. The missing front door: a lead creates ONE link,
--  pastes it in the team chat, and 25 teammates land in the right workspace
--  with the right role — no owner typing 25 emails, no pre-existing accounts.
--
--  TRUST PROPERTIES (deliberate, keep them):
--    * a link can only ever grant 'member' or 'viewer' — never lead/owner;
--      elevation stays an explicit owner action in the Members panel
--    * every link expires (max 30 days) and has a use cap (max 100)
--    * owners/leads can list + revoke live links at any time
--    * redemption is idempotent: re-clicking never duplicates or DOWNGRADES
--      an existing membership
--    * clients never touch the table directly — SECURITY DEFINER RPCs only,
--      RLS forced with no policies as the backstop
-- ============================================================================

create extension if not exists pgcrypto;   -- gen_random_uuid

create table if not exists workspace_invites (
    token        uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces(id) on delete cascade,
    role         text not null default 'member'
                 check (role in ('member', 'viewer')),
    created_by   uuid not null,
    created_at   timestamptz not null default now(),
    expires_at   timestamptz not null,
    max_uses     int  not null default 30 check (max_uses between 1 and 100),
    use_count    int  not null default 0,
    revoked      boolean not null default false
);
create index if not exists idx_wsi_workspace on workspace_invites (workspace_id);

-- No direct client access at all: RPCs are the only door.
alter table workspace_invites enable row level security;
alter table workspace_invites force row level security;
revoke all on workspace_invites from anon, authenticated;

-- ---------------------------------------------------------------------------
-- create_workspace_invite: owner/lead mints a link for their workspace
-- ---------------------------------------------------------------------------
create or replace function create_workspace_invite(
        ws uuid, invite_role text default 'member',
        ttl_hours int default 168, uses int default 30)
returns uuid
language plpgsql security definer set search_path = public as $$
declare
    tok uuid;
begin
    perform _require_member_admin(ws);          -- owner/lead check (existing)
    if invite_role not in ('member', 'viewer') then
        raise exception 'invite links can only grant member or viewer';
    end if;
    if ttl_hours < 1 or ttl_hours > 720 then     -- 30 days hard cap
        raise exception 'invite lifetime must be between 1 hour and 30 days';
    end if;
    if uses < 1 or uses > 100 then
        raise exception 'invite use cap must be between 1 and 100';
    end if;
    insert into workspace_invites (workspace_id, role, created_by,
                                   expires_at, max_uses)
    values (ws, invite_role, auth.uid(),
            now() + make_interval(hours => ttl_hours), uses)
    returning token into tok;
    return tok;
end;
$$;

-- ---------------------------------------------------------------------------
-- redeem_workspace_invite: any SIGNED-IN user joins via a live token
-- ---------------------------------------------------------------------------
create or replace function redeem_workspace_invite(invite_token uuid)
returns table (workspace_id uuid, workspace_name text, granted_role text)
language plpgsql security definer set search_path = public as $$
declare
    inv workspace_invites%rowtype;
    already boolean;
begin
    if auth.uid() is null then
        raise exception 'sign in first, then open the invite link again';
    end if;
    -- lock the row so two simultaneous redemptions can't both take the last use
    select * into inv from workspace_invites
     where token = invite_token for update;
    if not found or inv.revoked then
        raise exception 'this invite link is no longer valid — ask a team lead for a new one';
    end if;
    if inv.expires_at < now() then
        raise exception 'this invite link has expired — ask a team lead for a new one';
    end if;

    select exists (select 1 from workspace_members m
                    where m.workspace_id = inv.workspace_id
                      and m.user_id = auth.uid()) into already;
    if not already then
        if inv.use_count >= inv.max_uses then
            raise exception 'this invite link has reached its use limit — ask a team lead for a new one';
        end if;
        insert into workspace_members (workspace_id, user_id, role)
        values (inv.workspace_id, auth.uid(), inv.role);
        update workspace_invites set use_count = use_count + 1
         where token = invite_token;
    end if;
    -- already a member: idempotent no-op — existing role is NEVER downgraded

    return query
        select w.id, w.name,
               (select m.role from workspace_members m
                 where m.workspace_id = w.id and m.user_id = auth.uid())
          from workspaces w where w.id = inv.workspace_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- list_workspace_invites: owner/lead sees live links (for the revoke UI)
-- ---------------------------------------------------------------------------
create or replace function list_workspace_invites(ws uuid)
returns table (token uuid, role text, expires_at timestamptz,
               max_uses int, use_count int, revoked boolean)
language plpgsql stable security definer set search_path = public as $$
begin
    perform _require_member_admin(ws);
    return query
        select i.token, i.role, i.expires_at, i.max_uses, i.use_count, i.revoked
          from workspace_invites i
         where i.workspace_id = ws
           and not i.revoked
           and i.expires_at > now()
         order by i.created_at desc;
end;
$$;

-- ---------------------------------------------------------------------------
-- revoke_workspace_invite: owner/lead kills a link immediately
-- ---------------------------------------------------------------------------
create or replace function revoke_workspace_invite(invite_token uuid)
returns void
language plpgsql security definer set search_path = public as $$
declare
    ws uuid;
begin
    select workspace_id into ws from workspace_invites where token = invite_token;
    if not found then
        raise exception 'no such invite';
    end if;
    perform _require_member_admin(ws);
    update workspace_invites set revoked = true where token = invite_token;
end;
$$;

grant execute on function create_workspace_invite(uuid, text, int, int) to authenticated;
grant execute on function redeem_workspace_invite(uuid) to authenticated;
grant execute on function list_workspace_invites(uuid) to authenticated;
grant execute on function revoke_workspace_invite(uuid) to authenticated;

-- ==============================================================================
-- ||             6 · PROJECT VERSION HISTORY (snapshot-on-write)              ||
-- ==============================================================================
-- Server-side snapshot trigger keeping the last 20 versions of the project
-- blob. Run AFTER workspace_isolation.sql (needs is_workspace_member()).
-- source file: suspension/project_history.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — project version history (snapshot-on-write, server-side)
--  Run once in the Supabase SQL editor. Idempotent — safe to re-run.
--
--  WHY: the project ledger is one JSONB blob per (workspace, project). Even
--  with optimistic locking in the app, a bad merge, a bug, or a user clicking
--  through a conflict can still destroy data. This trigger snapshots the
--  PREVIOUS blob on every overwrite, entirely server-side, so recovery never
--  depends on the app having done the right thing. Keeps the last 20 versions
--  per project (a blob is ~1 MB, so worst case ~20 MB/project — bounded).
--
--  Run order: AFTER suspension/workspace_isolation.sql (needs its tables +
--  the is_workspace_member() helper it defines; if your helper is named
--  differently, adjust the two policy lines marked below).
-- ============================================================================

-- 1) History table --------------------------------------------------------
create table if not exists kinematik_project_history (
    hist_id       bigint generated always as identity primary key,
    workspace_id  uuid  not null,
    id            text  not null,
    data          jsonb not null,
    -- the overwritten row's own stamps, for point-in-time restore
    was_updated_at timestamptz,
    replaced_at    timestamptz not null default now()
);

create index if not exists kinematik_project_history_lookup
    on kinematik_project_history (workspace_id, id, replaced_at desc);

-- 2) Snapshot trigger ------------------------------------------------------
-- SECURITY DEFINER so the snapshot insert works under RLS regardless of the
-- writing user's own grants; the function only ever copies the row being
-- replaced, so it cannot be used to read or write anything else.
create or replace function kinematik_snapshot_project()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    -- Only snapshot when the blob actually changed (a no-op save costs nothing)
    if OLD.data is distinct from NEW.data then
        insert into kinematik_project_history
            (workspace_id, id, data, was_updated_at)
        values
            (OLD.workspace_id, OLD.id, OLD.data, OLD.updated_at);

        -- Prune: keep the newest 20 snapshots for this project
        delete from kinematik_project_history h
        where h.workspace_id = OLD.workspace_id
          and h.id = OLD.id
          and h.hist_id not in (
              select hist_id from kinematik_project_history
              where workspace_id = OLD.workspace_id and id = OLD.id
              order by replaced_at desc, hist_id desc
              limit 20);
    end if;
    return NEW;
end;
$$;

drop trigger if exists trg_kinematik_snapshot_project
    on kinematik_workspace_projects;
create trigger trg_kinematik_snapshot_project
    before update on kinematik_workspace_projects
    for each row execute function kinematik_snapshot_project();

-- 3) RLS: members may READ their workspace's history; nobody writes directly
--    (only the trigger inserts, and it runs as the definer).
alter table kinematik_project_history enable row level security;
alter table kinematik_project_history force row level security;

drop policy if exists history_member_read on kinematik_project_history;
create policy history_member_read on kinematik_project_history
    for select
    using (
        exists (
            select 1 from workspace_members m           -- << adjust if your
            where m.workspace_id = kinematik_project_history.workspace_id
              and m.user_id = auth.uid()                -- << membership check
        )                                               --    differs
    );

revoke insert, update, delete on kinematik_project_history from anon, authenticated;
grant  select on kinematik_project_history to authenticated;

-- 4) Restore recipe (manual, read-only to run; write the restore explicitly):
--    select hist_id, replaced_at, was_updated_at
--      from kinematik_project_history
--     where workspace_id = '<ws-uuid>' and id = 'default'
--     order by replaced_at desc;
--
--    update kinematik_workspace_projects p
--       set data = h.data, updated_at = now()
--      from kinematik_project_history h
--     where h.hist_id = <chosen hist_id>
--       and p.workspace_id = h.workspace_id and p.id = h.id;
--    (The restore itself triggers a snapshot of what it replaced — so even a
--     wrong restore is recoverable.)

-- ==============================================================================
-- ||                  7 · MASTER ASSEMBLY SCHEMA (optional)                   ||
-- ==============================================================================
-- Versioned assembly / CAD-part tables. Run AFTER workspace_isolation.sql.
-- Only needed if you use the master-assembly feature; harmless otherwise.
-- source file: suspension/master_assembly_schema.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Master Assembly schema (versioned progressive compilation).
--  Idempotent; run AFTER workspace_isolation.sql (needs workspaces +
--  is_workspace_member()).  All linear dims in mm, SAE axes (x rear, y right,
--  z up) — matching fullcar3d / cad_share.  Metric core; imperial is a
--  display-only concern and never touches this schema.
-- ============================================================================
create extension if not exists pgcrypto;

-- 1 ── Sub-assembly tree (recursive parent-child, one row per node) ----------
create table if not exists assemblies (
    id            uuid primary key default gen_random_uuid(),
    workspace_id  uuid not null references workspaces(id) on delete cascade,
    parent_id     uuid references assemblies(id) on delete cascade,
    name          text not null,
    subsystem     text not null check (subsystem in
                    ('chassis','powertrain','suspension','aero','electronics','other')),
    sort_order    int  not null default 0,
    created_at    timestamptz not null default now(),
    unique (workspace_id, parent_id, name)
);
create index if not exists idx_asm_ws_parent on assemblies (workspace_id, parent_id);

-- 2 ── Slots: positions parts occupy; anchors bind them to kinematics --------
create table if not exists assembly_slots (
    id             uuid primary key default gen_random_uuid(),
    workspace_id   uuid not null references workspaces(id) on delete cascade,
    assembly_id    uuid not null references assemblies(id) on delete cascade,
    slot_key       text not null,
    display_name   text not null,
    anchor_refs    jsonb not null,
    primary_axis_pair int[] not null default '{0,1}',
    secondary_ref  int not null default 2,
    dummy_shape    text not null default 'box'
                     check (dummy_shape in ('box','cylinder')),
    dummy_margin_mm  real not null default 8.0,
    dummy_min_dim_mm real not null default 15.0,
    dummy_radius_mm  real,
    fit_tolerance_mm real not null default 1.5,
    criticality    real not null default 1.0,
    created_at     timestamptz not null default now(),
    unique (workspace_id, slot_key)
);
create index if not exists idx_slots_ws_asm on assembly_slots (workspace_id, assembly_id);

-- 3 ── Logical parts and immutable versions (branching DAG) ------------------
create table if not exists cad_parts (
    id            uuid primary key default gen_random_uuid(),
    workspace_id  uuid not null references workspaces(id) on delete cascade,
    part_number   text not null,
    name          text not null,
    default_slot  uuid references assembly_slots(id) on delete set null,
    created_by    uuid,
    created_at    timestamptz not null default now(),
    unique (workspace_id, part_number)
);

create table if not exists cad_part_versions (
    id                 uuid primary key default gen_random_uuid(),
    workspace_id       uuid not null references workspaces(id) on delete cascade,
    part_id            uuid not null references cad_parts(id) on delete cascade,
    parent_version_id  uuid references cad_part_versions(id),
    rev_label          text not null,
    commit_message     text not null default '',
    source_bucket_path text not null,          -- bucket 'cad-parts'
    render_bucket_path text,                   -- server-converted .glb
    sha256             text not null,
    file_format        text not null,
    file_size_bytes    bigint not null,
    source_units       text not null default 'mm',
    bbox_l_mm real not null, bbox_w_mm real not null, bbox_h_mm real not null,
    bbox_center_mm     real[3] not null default '{0,0,0}',
    reg_translation_mm real[3],
    reg_quaternion     real[4],
    reg_uniform_scale  real not null default 1.0,
    reg_residual_mm    real,
    reg_confidence     text not null default 'unregistered'
                         check (reg_confidence in
                           ('unregistered','low_confidence','roll_assumed','solved')),
    connector_points   jsonb,
    mass_kg            real,
    created_by         uuid,
    created_at         timestamptz not null default now(),
    unique (workspace_id, part_id, rev_label),
    unique (workspace_id, sha256, part_id)
);
create index if not exists idx_cpv_part   on cad_part_versions (workspace_id, part_id);
create index if not exists idx_cpv_parent on cad_part_versions (parent_version_id);

-- 4 ── Master Assembly state: commit chain + per-slot entries ----------------
create table if not exists assembly_commits (
    id               uuid primary key default gen_random_uuid(),
    workspace_id     uuid not null references workspaces(id) on delete cascade,
    parent_commit_id uuid references assembly_commits(id),
    branch           text not null default 'main',
    commit_hash      text not null,
    message          text not null default '',
    author           uuid,
    created_at       timestamptz not null default now(),
    unique (workspace_id, commit_hash)
);
create index if not exists idx_ac_ws_branch_t
    on assembly_commits (workspace_id, branch, created_at desc);

create table if not exists assembly_commit_entries (
    commit_id       uuid not null references assembly_commits(id) on delete cascade,
    workspace_id    uuid not null references workspaces(id) on delete cascade,
    slot_id         uuid not null references assembly_slots(id) on delete cascade,
    occupancy       text not null check (occupancy in ('dummy','cad','empty')),
    part_version_id uuid references cad_part_versions(id),
    dummy_params    jsonb,
    check ((occupancy = 'cad') = (part_version_id is not null)),
    primary key (commit_id, slot_id)
);

create table if not exists assembly_branch_heads (
    workspace_id uuid not null references workspaces(id) on delete cascade,
    branch       text not null,
    commit_id    uuid not null references assembly_commits(id),
    updated_at   timestamptz not null default now(),
    primary key (workspace_id, branch)
);

-- 5 ── Interference / constraint-violation ledger -----------------------------
create table if not exists interference_flags (
    id            uuid primary key default gen_random_uuid(),
    workspace_id  uuid not null references workspaces(id) on delete cascade,
    commit_id     uuid references assembly_commits(id) on delete cascade,
    slot_id       uuid not null references assembly_slots(id) on delete cascade,
    other_slot_id uuid references assembly_slots(id),
    kind          text not null check (kind in
                    ('hardpoint_mismatch','aabb_overlap','mesh_intersection',
                     'degenerate_frame','unit_suspect','scale_clamped')),
    severity      text not null default 'warn'
                    check (severity in ('info','warn','block')),
    detail        jsonb not null default '{}'::jsonb,
    resolved_at   timestamptz,
    created_at    timestamptz not null default now()
);
create index if not exists idx_iflags_open
    on interference_flags (workspace_id, slot_id) where resolved_at is null;

-- 6 ── History immutability: commits/entries are append-only; versions allow
--      only registration + render-path completion after insert ---------------
create or replace function ma_reject_mutation() returns trigger
language plpgsql as $$
begin
    raise exception 'Master Assembly history is append-only (%s)', tg_table_name;
end $$;

drop trigger if exists trg_ac_immutable  on assembly_commits;
create trigger trg_ac_immutable  before update or delete on assembly_commits
    for each row execute function ma_reject_mutation();
drop trigger if exists trg_ace_immutable on assembly_commit_entries;
create trigger trg_ace_immutable before update or delete on assembly_commit_entries
    for each row execute function ma_reject_mutation();

create or replace function ma_guard_version_update() returns trigger
language plpgsql as $$
begin
    if (new.part_id, new.sha256, new.source_bucket_path, new.rev_label,
        new.parent_version_id, new.file_format, new.file_size_bytes)
       is distinct from
       (old.part_id, old.sha256, old.source_bucket_path, old.rev_label,
        old.parent_version_id, old.file_format, old.file_size_bytes) then
        raise exception 'cad_part_versions content is immutable; only registration and render fields may complete after insert';
    end if;
    return new;
end $$;
drop trigger if exists trg_cpv_guard on cad_part_versions;
create trigger trg_cpv_guard before update on cad_part_versions
    for each row execute function ma_guard_version_update();

-- 7 ── Time-travel helper + server-side ACI -----------------------------------
create or replace function assembly_commit_as_of(
    ws uuid, br text, as_of timestamptz) returns uuid
language sql stable as $$
    select id from assembly_commits
     where workspace_id = ws and branch = br and created_at <= as_of
     order by created_at desc limit 1
$$;

-- Volume-weighted, criticality-weighted, κ-discounted completion per commit.
create or replace view assembly_completion as
with per_slot as (
    select e.workspace_id, e.commit_id, s.criticality,
           case when e.occupancy = 'cad'
                then greatest(v.bbox_l_mm * v.bbox_w_mm * v.bbox_h_mm, 0)
                else greatest(
                     coalesce((e.dummy_params->>'l_mm')::real, 0) *
                     coalesce((e.dummy_params->>'w_mm')::real, 0) *
                     coalesce((e.dummy_params->>'h_mm')::real, 0), 0)
           end as vol,
           case when e.occupancy = 'cad' then
                case v.reg_confidence
                     when 'solved'         then 1.00
                     when 'roll_assumed'   then 0.90
                     when 'low_confidence' then 0.75
                     else 0.50 end
                else 0.0 end as kappa
      from assembly_commit_entries e
      join assembly_slots s on s.id = e.slot_id
      left join cad_part_versions v on v.id = e.part_version_id)
select workspace_id, commit_id,
       case when sum(criticality * vol) > 0
            then sum(criticality * vol * kappa) / sum(criticality * vol)
            else 0 end as aci,
       count(*) filter (where kappa > 0) as n_cad,
       count(*) as n_slots
  from per_slot group by workspace_id, commit_id;

-- 8 ── RLS: enable + force everywhere; membership-gated; WITH CHECK on writes -
do $$
declare t text;
begin
  foreach t in array array['assemblies','assembly_slots','cad_parts',
                           'cad_part_versions','assembly_commits',
                           'assembly_commit_entries','assembly_branch_heads',
                           'interference_flags'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists %I_sel on %I', t, t);
    execute format('drop policy if exists %I_ins on %I', t, t);
    execute format('drop policy if exists %I_upd on %I', t, t);
    execute format('drop policy if exists %I_del on %I', t, t);
    execute format($p$create policy %I_sel on %I for select
                     using (is_workspace_member(workspace_id))$p$, t, t);
    execute format($p$create policy %I_ins on %I for insert
                     with check (is_workspace_member(workspace_id))$p$, t, t);
    execute format($p$create policy %I_upd on %I for update
                     using (is_workspace_member(workspace_id))
                     with check (is_workspace_member(workspace_id))$p$, t, t);
    execute format($p$create policy %I_del on %I for delete
                     using (is_workspace_member(workspace_id))$p$, t, t);
  end loop;
end $$;

-- Storage: create a private bucket 'cad-parts' in the dashboard (or via
-- storage API), keyed workspace_id/part_id/sha256.ext — paths land in
-- cad_part_versions.source_bucket_path / render_bucket_path; sha256 is the
-- true identity (paths can be relocated; hashes cannot lie).

-- ==============================================================================
-- ||                    8 · ANALYTICS HARDENING (optional)                    ||
-- ==============================================================================
-- Patches analytics_schema.sql (data-quality guards, view fixes, the
-- known_features allow-list incl. docs & frames). Run any time after step 1.
-- Only needed if you use the analytics dashboards.
-- source file: suspension/analytics_hardening.sql
-- ------------------------------------------------------------------------------

-- ============================================================================
--  KinematiK — Analytics hardening migration
--
--  WHY THIS EXISTS
--  ----------------
--  analytics_schema.sql is sound, but an audit of the app found the gap isn't
--  in the SQL — it's that most tabs never call the instrumentation API at all.
--  Two consequences this migration fixes on the DATA side (the Python side is
--  fixed separately, see streamlit_app.py):
--
--    1. `feature` had no validation, so a typo'd feature string (or a feature
--       that was renamed/removed) silently creates an orphaned series with no
--       baseline and no membership in any real tab — it just vanishes from
--       every ROI/usage view with no error anywhere.
--    2. `feature_baselines` only had 9 of the 24 real tabs seeded. Even a tab
--       instrumented perfectly would show $0 / 0 hours saved forever because
--       there's nothing to join against.
--
--  This migration is idempotent — re-running it is safe.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  1. FEATURE ALLOW-LIST — single source of truth for valid feature ids
-- ----------------------------------------------------------------------------
--  Mirrors _TAB_META in streamlit_app.py exactly (24 tabs) plus 'mythbuster',
--  which is a real instrumented workflow but isn't a top-level tab. Keeping
--  this as DATA (not a hardcoded CHECK list) means adding a tab is a single
--  INSERT here, not a migration that touches the events table.
create table if not exists known_features (
    feature      text primary key,
    label        text not null,
    is_tab       boolean not null default true,   -- false for sub-workflows like 'mythbuster'
    -- Some tabs (kinematics, accumulator sizing) recompute live on every
    -- slider/number_input change with no explicit "run" or "save" button, so
    -- there is no honest moment to call workflow_complete without firing on
    -- every keystroke (which would inflate the number, not measure it). Mark
    -- those here so the coverage view explains the gap instead of just
    -- flagging it red as if it were an oversight.
    reactive_only boolean not null default false,
    added_at     timestamptz not null default now()
);

insert into known_features (feature, label, is_tab, reactive_only) values
    ('kinematics',  'Kinematics',              true, true),
    ('roll',        'Roll & Load Transfer',    true, false),
    ('grip',        'Grip Balance',            true, false),
    ('model3d',     '3D Model',                true, false),
    ('aero',        'Aerodynamics',            true, false),
    ('ev',          'EV Powertrain',           true, false),
    ('accum',       'Accumulator',             true, true),
    ('brakes',      'Brakes',                  true, false),
    ('cost',        'Cost & BOM',              true, false),
    ('compliance',  'Compliance (Flex)',       true, false),
    ('teamfit',     'Team Fit',                true, false),
    ('weight',      'Weight & Handover',       true, false),
    ('notes',       'Lead Notes',              true, false),
    ('tire',        'Tire & Grip',             true, false),
    ('setup',       'Setup Optimiser',         true, false),
    ('laptime',     'Lap Time',                true, false),
    ('ggv',         'GGV Diagram',             true, false),
    ('transient',   'Transient',               true, false),
    ('validation',  'Validation',              true, false),
    ('integration', 'Integration',             true, false),
    ('registry',    'Registry',                true, false),
    ('analytics',   'Analytics',               true, false),
    ('pcb',         'Electronics (PCB)',       true, false),
    ('tractive',    'Tractive Safety',         true, false),
    ('dfmea',       'DFMEA',                   true, false),
    ('docs',        'Documentation',           true, false),
    ('frames',      'Frames & Datums',         true, false),
    ('mythbuster',  'Mythbuster',              false, false)
on conflict (feature) do update
    set label = excluded.label, is_tab = excluded.is_tab,
        reactive_only = excluded.reactive_only;

comment on table known_features is
  'Allow-list of valid feature ids, mirroring _TAB_META in streamlit_app.py. '
  'Source of truth for the analytics_events.feature FK and for the coverage '
  'views below — add a row here whenever a tab is added in the app.';


-- ----------------------------------------------------------------------------
--  2. ENFORCE THE ALLOW-LIST — stop silent typos/orphans at insert time
-- ----------------------------------------------------------------------------
--  A foreign key (rather than a CHECK) so the allow-list can grow without a
--  migration. NULL feature stays legal (session_start has no feature).
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'ae_feature_fk'
    ) then
        alter table analytics_events
            add constraint ae_feature_fk
            foreign key (feature) references known_features (feature)
            on update cascade;
    end if;
end $$;


-- ----------------------------------------------------------------------------
--  3. BACKFILL feature_baselines — every tab needs a row or ROI is mute for it
-- ----------------------------------------------------------------------------
--  These are STARTING estimates (confidence='estimate'), same as the original
--  seed — leads should replace with timed numbers as they collect them. Tabs
--  already seeded in analytics_schema.sql are left untouched (on conflict do
--  nothing); this only fills the 15 that were missing.
insert into feature_baselines
    (feature, label, manual_minutes, in_tool_minutes, alternative,
     alternative_cost_usd, basis, confidence, notes) values
  ('roll',       'Roll & load transfer',      90,  5,  'spreadsheet', 0, 'lead_estimate','estimate', null),
  ('grip',       'Grip balance',              90,  5,  'spreadsheet', 0, 'lead_estimate','estimate', null),
  ('model3d',    '3D model review',           60,  5,  'CAD review',  0, 'lead_estimate','estimate', 'Cross-checking the assembled car by eye in CAD vs one rendered view.'),
  ('aero',       'Aero map / CFD setup',      240, 20, 'CFD by hand', 0, 'lead_estimate','estimate', null),
  ('ev',         'EV powertrain sizing',      180, 10, 'spreadsheet', 0, 'lead_estimate','estimate', null),
  ('compliance', 'Compliance / flex check',   120, 10, 'hand calc',   0, 'lead_estimate','estimate', null),
  ('teamfit',    'Team-fit / role planning',  45,  5,  'meeting',     0, 'lead_estimate','estimate', null),
  ('weight',     'Weight & handover tracking',60,  5,  'spreadsheet', 0, 'lead_estimate','estimate', null),
  ('notes',      'Lead notes / handover log', 30,  5,  'Discord/Docs',0, 'lead_estimate','estimate', null),
  ('tire',       'Tire & grip modelling',     150, 10, 'TTC by hand', 0, 'lead_estimate','estimate', null),
  ('setup',      'Setup optimiser',           120, 8,  'trial & error',0,'lead_estimate','estimate', null),
  ('transient',  'Transient analysis',        150, 10, 'spreadsheet', 0, 'lead_estimate','estimate', null),
  ('validation', 'Cross-tab validation',      60,  5,  'meeting',     0, 'lead_estimate','estimate', null),
  ('pcb',        'Electronics / PCB check',   90,  10, 'hand calc',   0, 'lead_estimate','estimate', null),
  ('tractive',   'Tractive system safety',    120, 10, 'rules doc',   0, 'lead_estimate','estimate', null),
  ('mythbuster', 'Myth-check a claim',        20,  2,  'meeting/Discord debate', 0, 'lead_estimate','estimate',
                 'Replaces a back-and-forth argument thread with an instant ruled check.')
on conflict (feature) do nothing;


-- ----------------------------------------------------------------------------
--  4. COVERAGE / GAP-DETECTION VIEWS — catch the next silent gap automatically
-- ----------------------------------------------------------------------------

-- Every known tab vs whether it has ANY event at all, and how long since the
-- last one. The board number is only honest if every tab here shows recent
-- activity OR a known reason it doesn't (e.g. a brand-new tab).
create or replace view v_instrumentation_coverage as
select
    k.feature,
    k.label,
    k.is_tab,
    count(e.id)                          as total_events,
    count(e.id) filter (where e.event_type = 'tab_open')          as tab_opens,
    count(e.id) filter (where e.event_type = 'feature_engage')    as engagements,
    count(e.id) filter (where e.event_type = 'workflow_complete') as completions,
    max(e.occurred_at)                   as last_event_at,
    (b.feature is not null)              as has_baseline,
    case
        when count(e.id) = 0 then 'NOT INSTRUMENTED — zero events ever'
        when max(e.occurred_at) < now() - interval '14 days'
            then 'STALE — no events in 14+ days'
        when b.feature is null then 'NO BASELINE — usage logs but ROI can''t compute'
        else 'OK'
    end                                   as status
from known_features k
left join analytics_events e on e.feature = k.feature
left join feature_baselines b on b.feature = k.feature
group by k.feature, k.label, k.is_tab, b.feature
order by (count(e.id) = 0) desc, k.feature;

comment on view v_instrumentation_coverage is
  'Run this before trusting any ROI/usage number. Any row that is not ''OK'' '
  'means that tab''s numbers are silently zero/incomplete, not actually zero '
  'usage.';

-- One-row health check for a dashboard banner / CI assertion.
create or replace view v_instrumentation_health as
select
    count(*)                                            as known_features,
    count(*) filter (where total_events = 0)            as features_never_logged,
    count(*) filter (where status = 'STALE — no events in 14+ days') as features_stale,
    count(*) filter (where not has_baseline)             as features_missing_baseline,
    count(*) filter (where status = 'OK')                as features_healthy,
    round(100.0 * count(*) filter (where status = 'OK') / nullif(count(*), 0), 1)
                                                          as pct_healthy
from v_instrumentation_coverage;

-- Events that reference a feature NOT in the allow-list slipped through before
-- the FK existed (historical rows) — surface them rather than silently drop.
-- (After this migration, new rows like this are rejected at insert time.)
create or replace view v_orphaned_feature_events as
select e.feature, count(*) as n, min(e.occurred_at) as first_seen, max(e.occurred_at) as last_seen
from analytics_events e
left join known_features k on k.feature = e.feature
where e.feature is not null and k.feature is null
group by e.feature
order by n desc;

comment on view v_orphaned_feature_events is
  'Should be empty going forward (the FK blocks new orphans). Non-empty rows '
  'here are pre-migration events with a feature id that never matched a real '
  'tab — likely a typo or a renamed feature; rename or remap manually.';


-- ----------------------------------------------------------------------------
--  5. FIX v_individual_use — anonymous rows were keyed by SESSION, not PERSON
-- ----------------------------------------------------------------------------
--  The original view's `who` was `coalesce(member, 'anon:' || left(session_id,
--  8))`. session_id is minted fresh every browser session (by design, so every
--  logon produces a session_start event for foot-traffic counting) — so one
--  anonymous student who opens the app five times across a week shows up as
--  FIVE different "anon:xxxxxxxx" rows in the per-member table, each looking
--  like a separate person who used the tool once. For a board/sponsor pitch
--  this UNDERCOUNTS engagement per person and OVERCOUNTS headcount — the
--  opposite of what "individual use" should show.
--
--  v_retention already solved this correctly elsewhere in the schema: identity
--  for a person who didn't type a name should fall back to the DURABLE
--  visitor_id (persisted client-side, survives across visits), only falling
--  back further to session_id if even that's missing. This redefinition makes
--  v_individual_use consistent with v_retention instead of contradicting it.
drop view if exists v_individual_use cascade;
create view v_individual_use as
select
    coalesce(nullif(member, ''), 'anon-' || left(visitor_id, 8), 'anon-' || left(session_id, 8))
                                                       as who,
    coalesce(
        (array_agg(subteam ORDER BY occurred_at DESC)
            filter (where subteam is not null and subteam <> 'unknown'))[1],
        'unknown')                                     as subteam,
    count(distinct session_id)                        as sessions,
    count(*) filter (where event_type = 'feature_engage') as feature_uses,
    count(*) filter (where event_type = 'workflow_complete') as workflows_completed,
    count(distinct feature)                           as distinct_features_used,
    min(occurred_at)                                  as first_seen,
    max(occurred_at)                                  as last_seen,
    (member is not null)                              as is_named
from analytics_events
group by 1, 9
order by feature_uses desc;

comment on view v_individual_use is
  'Per-person activity. Anonymous people are grouped by their durable '
  'visitor_id (one row per browser, across all their visits), not by '
  'session_id (which would split one person into one row per visit). '
  'subteam is the person''s MOST RECENT non-unknown subteam — NOT max(), '
  'which used to wrongly pick the ''unknown'' placeholder over real subteams '
  'like brakes since ''unknown'' sorts after them alphabetically. '
  '`is_named` distinguishes a real entered name from an anon-prefixed id so '
  'the UI can label/sort/filter them honestly instead of presenting both '
  'the same way.';


-- ----------------------------------------------------------------------------
--  6. FIX v_comparison_to_alternatives — OptimumK licence cost was $0
-- ----------------------------------------------------------------------------
--  The "vs. the alternatives" board table reads `alternative_annual_cost_usd`
--  straight from `sum(alternative_cost_usd)` in feature_baselines. Every seed
--  row defaults to 0 — correct for hand calc / spreadsheet / meeting / Discord
--  hunt (genuinely free), but WRONG for the kinematics row, which names
--  'OptimumK' as the alternative and is a real, paid commercial product. A
--  named commercial competitor showing $0/yr undersells the actual avoided
--  cost and is the kind of number a sponsor or faculty reviewer could check
--  themselves and catch.
--
--  Sourced: OptimumG's current student software store lists OptimumKinematics
--  Student Edition at $295.00 US/year (https://students.optimumg.com/software/,
--  fetched 2026-06-29). This is the base kinematics module only — Forces and
--  Optimization add-ons are priced separately ($50/yr and $295/yr) and are
--  NOT included here since this feature replaces base kinematics analysis,
--  not those add-on workflows.
update feature_baselines
set alternative_cost_usd = 295.00,
    notes = coalesce(notes || ' ', '')
            || '[Licence cost: OptimumKinematics Student Edition, $295 US/yr, '
            || 'per OptimumG''s current student store — '
            || 'https://students.optimumg.com/software/, checked 2026-06-29.]',
    updated_at = now()
where feature = 'kinematics'
  and alternative = 'OptimumK'
  and coalesce(notes, '') not like '%OptimumKinematics Student Edition%';


-- ----------------------------------------------------------------------------
--  7. FIX v_comparison_to_alternatives — claimed a confidence flag it lacked
-- ----------------------------------------------------------------------------
--  The analytics tab's caption under this exact table says "each row carries
--  a confidence flag so the board sees measured vs estimated honestly." That
--  flag exists on feature_baselines and is surfaced in v_hours_saved_by_feature
--  — but was NEVER added to v_comparison_to_alternatives, so the caption
--  overstated what this specific table actually showed. Since this view
--  aggregates potentially several features per alternative (e.g. multiple
--  features might cite "spreadsheet"), a single yes/no isn't quite right —
--  surface whether ALL underlying baselines are measured, so a row that
--  mixes one measured + one estimated feature doesn't get silently
--  presented as fully confirmed.
-- ----------------------------------------------------------------------------
--  7. v_comparison_to_alternatives confidence rollup — folded into section 12
-- ----------------------------------------------------------------------------
--  NOTE: this used to be its own CREATE OR REPLACE VIEW here, immediately
--  superseded by two more CREATE OR REPLACE VIEW statements later in this
--  same file (sections 10 and 12) as more columns were added. Three
--  sequential replaces of the same view, even though each one is a valid
--  append on the one before it, turned out to be fragile in practice — if
--  this file gets re-run partially, out of order, or against a database that
--  already has a LATER version of the view live (e.g. from a prior full run),
--  Postgres can see an intermediate replace as trying to DROP columns that
--  already exist live (42P16), even though the columns were never actually
--  removed from the file's final intent.
--
--  Fixed by collapsing all three into the single, final, complete view
--  definition in section 12 below — the confidence-flag fields described
--  here (all_measured / n_measured / n_baselines) are included there.


-- ----------------------------------------------------------------------------
--  8. ADD SolidWorks + ANSYS as named alternatives (model3d, compliance)
-- ----------------------------------------------------------------------------
--  model3d's baseline (from this same migration, section 3) cited the generic
--  "CAD review" as its alternative; compliance cited generic "hand calc". Both
--  are accurate in spirit but vague — the real, specific tool an FSAE team
--  reaches for is SolidWorks (CAD/assembly review) and ANSYS (FEA-based
--  compliance/flex checks), so naming them is more credible on a board slide
--  than a generic label.
--
--  Both vendors do run dedicated free sponsorship programs for FSAE student
--  teams (confirmed on their own current sites, fetched 2026-06-29 — see
--  section 9 for citations), so a team WITHOUT another arrangement would
--  often pay $0. But cost isn't fixed across every team or audience: elbee
--  racing's actual access here is a PAID university site licence rather than
--  the free program (per direct correction from the team), and a COMPANY
--  evaluating KinematiK would face full commercial pricing, not either of
--  these. Section 9 below splits the cost into three explicit tiers instead
--  of asserting one number that's only true for one situation; this section
--  only renames the alternative to the specific real tool.
update feature_baselines
set alternative = 'SolidWorks',
    updated_at = now()
where feature = 'model3d'
  and coalesce(alternative, '') <> 'SolidWorks';

update feature_baselines
set alternative = 'ANSYS',
    updated_at = now()
where feature = 'compliance'
  and coalesce(alternative, '') <> 'ANSYS';


-- ----------------------------------------------------------------------------
--  9. SPLIT licence cost into THREE tiers — one number was hiding the audience
-- ----------------------------------------------------------------------------
--  Direct correction from the team: SolidWorks and ANSYS are NOT free for
--  elbee racing — the university pays for the licence; it's a real cost, just
--  not one that lands on the team's own budget line. And separately: if
--  KinematiK is ever shown to a COMPANY rather than a student team, "$0
--  because of FSAE sponsorship" is the wrong comparison entirely — a company
--  pays full commercial retail, and that's the number that matters to them.
--
--  One `alternative_cost_usd` column can't honestly hold three different true
--  numbers for three different audiences at once. Splitting it:
--    cost_team_usd        — what the STUDENT TEAM itself pays out of pocket.
--                            $0 for SolidWorks/ANSYS (FSAE sponsorship), real
--                            for OptimumK (no sponsorship program found).
--    cost_academic_usd    — the paid academic/university tier, when the team's
--                            access is actually a university site licence or a
--                            paid research/teaching license rather than the
--                            free student-team program. NULL where unknown —
--                            university contracts are typically negotiated and
--                            not public, so this is left for the team to fill
--                            in from their own university's actual invoice
--                            rather than guess at a number we can't verify.
--    cost_commercial_usd  — full retail/commercial price, the number a
--                            COMPANY evaluating KinematiK would actually face.
--                            Sourced low end of each vendor's published range
--                            (entry tier, single seat, USD/yr):
--                              OptimumKinematics: $295/yr is ALREADY the
--                                public price (no separate "commercial" tier
--                                publicly listed) — students.optimumg.com,
--                                checked 2026-06-29.
--                              SolidWorks: Standard tier, $2,820/yr/seat —
--                                solidworks.com/how-to-buy/solidworks-design-
--                                cloud-services-plans-pricing, checked
--                                2026-06-29.
--                              ANSYS: entry-level commercial lease, ~$5,000/yr/
--                                seat per multiple independent pricing guides
--                                (itqlick.com, thepricer.org), checked
--                                2026-06-29 — Ansys does not publish a single
--                                list price, so this is the low end of a wide,
--                                heavily-negotiated range, not a quote.
--  `alternative_cost_usd` (the original column) is kept and now means the same
--  thing as `cost_team_usd`, so v_comparison_to_alternatives and any existing
--  consumer of that column keep working unchanged.
alter table feature_baselines
    add column if not exists cost_team_usd numeric,
    add column if not exists cost_academic_usd numeric,
    add column if not exists cost_commercial_usd numeric;

comment on column feature_baselines.cost_team_usd is
  'What the student team itself pays out of pocket. $0 only when a real, '
  'named sponsorship program covers it (see notes for the source) — not a '
  'default assumption.';
comment on column feature_baselines.cost_academic_usd is
  'Paid academic/university tier (site licence, research/teaching license) '
  'when access isn''t the free student-team program. University contracts '
  'are typically negotiated/NDA''d — NULL means "ask your university what '
  'they actually pay", not "this is free".';
comment on column feature_baselines.cost_commercial_usd is
  'Full commercial/retail price — the number that matters if KinematiK is '
  'ever pitched to a company rather than a student team. Sourced to the low '
  'end of each vendor''s published range; treat as a floor, not a quote.';

update feature_baselines
set cost_team_usd = 295.00, cost_commercial_usd = 295.00,
    alternative_cost_usd = 295.00
where feature = 'kinematics' and alternative = 'OptimumK';

update feature_baselines
set cost_team_usd = 0, cost_commercial_usd = 2820.00,
    alternative_cost_usd = 0,
    notes = coalesce(notes || ' ', '')
            || '[Correction from the team: elbee racing''s actual access is '
            || 'via a PAID university site licence, not the free FSAE '
            || 'sponsorship — the cost is real, just paid by the university '
            || 'rather than the team''s own budget. Fill in cost_academic_usd '
            || 'with the real contracted figure if/when known; left NULL '
            || 'here because university licence terms are typically '
            || 'negotiated and not public.]'
where feature = 'model3d' and alternative = 'SolidWorks'
  and coalesce(notes, '') not like '%PAID university site licence%';

update feature_baselines
set cost_team_usd = 0, cost_commercial_usd = 5000.00,
    alternative_cost_usd = 0,
    notes = coalesce(notes || ' ', '')
            || '[Correction from the team: elbee racing''s actual access is '
            || 'via a PAID university site licence, not the free FSAE '
            || 'sponsorship — the cost is real, just paid by the university '
            || 'rather than the team''s own budget. Fill in cost_academic_usd '
            || 'with the real contracted figure if/when known; left NULL '
            || 'here because university licence terms are typically '
            || 'negotiated and not public.]'
where feature = 'compliance' and alternative = 'ANSYS'
  and coalesce(notes, '') not like '%PAID university site licence%';

comment on table feature_baselines is
  'Per-feature manual-vs-in-tool minute estimates that drive the hours-saved
   ROI, plus per-alternative licence cost split by audience (team/academic/
   commercial — see cost_team_usd, cost_academic_usd, cost_commercial_usd).
   Edit these to tune the board number; each carries a basis + confidence so
   the estimate is defensible.';


-- ----------------------------------------------------------------------------
--  10. v_comparison_to_alternatives commercial-cost column — folded into 12
-- ----------------------------------------------------------------------------
--  Same reasoning as section 7's note above: this used to be its own
--  CREATE OR REPLACE VIEW adding commercial_annual_cost_usd, immediately
--  superseded by section 12's final version. Collapsed for the same
--  re-run-safety reason — see section 7.




-- ----------------------------------------------------------------------------
--  11. FILL IN cost_academic_usd — a blank field reads as "didn't check"
-- ----------------------------------------------------------------------------
--  Section 9 left cost_academic_usd NULL because a specific university's
--  negotiated contract number genuinely can't be known without asking them.
--  But "I don't know YOUR exact number" isn't the same as "there's no public
--  reference point" — leaving a board-facing field blank reads as "we never
--  checked", which is worse than a clearly-sourced range. Real academic/
--  research-tier pricing for both tools, confirmed from primary sources
--  (university software stores, not marketing pages), checked 2026-06-29:
--
--    SolidWorks Academic Research license: UBC Physics & Astronomy's own
--    published cost breakdown (a real department's actual procurement
--    research, not a vendor page) states $2,000-$4,000 per seat to purchase,
--    plus $650-$1,000/yr recurring maintenance per seat —
--    https://phas.ubc.ca/solidworks-academic-research-usage
--
--    ANSYS Academic research license: University of Illinois's own current
--    WebStore lists their Academic Multiphysics Campus Research License at
--    $330/yr (expires 2026-06-30, so this is a live, current listing, not
--    historical) — https://webstore.illinois.edu/shop/product.aspx?zpid=6070
--    Independent pricing guides (thepricer.org, citing the same UIUC listing
--    plus other academic-tier reports) note academic/research ANSYS pricing
--    can run well into the thousands depending on module bundle and HPC core
--    count, so $330/yr is a real but LOW-end anchor, not a typical figure.
--
--  Stored as low/high pairs (not a single midpoint) because the real spread
--  is wide and a fabricated midpoint would imply false precision. elbee
--  racing's own university contract may differ from either reference — these
--  are defensible public anchors to cite until the team's actual invoice
--  number is known, not a substitute for it.
alter table feature_baselines
    add column if not exists cost_academic_low_usd numeric,
    add column if not exists cost_academic_high_usd numeric;

comment on column feature_baselines.cost_academic_low_usd is
  'Low end of a sourced public academic/research licence price range (see '
  'notes for citation). Not elbee racing''s actual contracted number unless '
  'confirmed — a defensible public anchor, not a substitute for the real '
  'invoice.';
comment on column feature_baselines.cost_academic_high_usd is
  'High end of the same sourced range.';

update feature_baselines
set cost_academic_usd = 3000.00,  -- midpoint of the sourced range, for any
                                   -- consumer that only reads a single value
    cost_academic_low_usd = 2000.00,
    cost_academic_high_usd = 4650.00,  -- 4000 purchase + ~1000 yr-1 maintenance, high end
    notes = coalesce(notes || ' ', '')
            || '[Academic tier reference: UBC Physics & Astronomy reports '
            || 'SolidWorks Academic Research licenses at $2,000-$4,000/seat '
            || 'purchase + $650-$1,000/yr maintenance — '
            || 'https://phas.ubc.ca/solidworks-academic-research-usage, '
            || 'checked 2026-06-29. This is a public reference point, not '
            || 'elbee''s confirmed contract figure.]'
where feature = 'model3d' and alternative = 'SolidWorks'
  and coalesce(notes, '') not like '%Academic tier reference%';

update feature_baselines
set cost_academic_usd = 330.00,  -- UIUC's published current price; genuinely
                                   -- the low end — see notes
    cost_academic_low_usd = 330.00,
    cost_academic_high_usd = 5000.00,  -- low end of independently-reported
                                         -- academic/research bundles running
                                         -- into the thousands
    notes = coalesce(notes || ' ', '')
            || '[Academic tier reference: University of Illinois WebStore '
            || 'lists their current Academic Multiphysics Campus Research '
            || 'License at $330/yr — '
            || 'https://webstore.illinois.edu/shop/product.aspx?zpid=6070, '
            || 'checked 2026-06-29. This is a real but low-end anchor — '
            || 'other academic/research ANSYS tiers run well into the '
            || 'thousands depending on bundle. Not elbee''s confirmed '
            || 'contract figure.]'
where feature = 'compliance' and alternative = 'ANSYS'
  and coalesce(notes, '') not like '%Academic tier reference%';


-- ----------------------------------------------------------------------------
--  12. SURFACE the academic tier in v_comparison_to_alternatives
-- ----------------------------------------------------------------------------
create or replace view v_comparison_to_alternatives as
select
    f.alternative,
    count(distinct f.feature)                       as features_replacing,
    round(avg(f.manual_minutes)::numeric, 1)        as avg_manual_minutes,
    round(avg(f.in_tool_minutes)::numeric, 1)       as avg_in_tool_minutes,
    round(avg(f.manual_minutes - f.in_tool_minutes)::numeric, 1) as avg_minutes_saved_each,
    round((100.0 * (1 - avg(f.in_tool_minutes) / nullif(avg(f.manual_minutes), 0)))::numeric, 0)
                                                    as pct_faster,
    sum(f.alternative_cost_usd)                     as alternative_annual_cost_usd,
    bool_and(f.confidence = 'measured')             as all_measured,
    count(*) filter (where f.confidence = 'measured') as n_measured,
    count(*)                                         as n_baselines,
    sum(coalesce(f.cost_commercial_usd, f.alternative_cost_usd)) as commercial_annual_cost_usd,
    sum(f.cost_academic_low_usd)                     as academic_low_annual_cost_usd,
    sum(f.cost_academic_high_usd)                    as academic_high_annual_cost_usd
from feature_baselines f
where f.alternative is not null
group by f.alternative
order by avg_minutes_saved_each desc;

comment on view v_comparison_to_alternatives is
  'Per-alternative comparison (KinematiK vs hand calc / Excel / OptimumK /
   SolidWorks / ANSYS / etc). Three cost lenses, pick the one matching your
   audience: alternative_annual_cost_usd = what THIS TEAM pays (often $0 via
   sponsorship, or a real but uninvoiced-to-the-team university cost);
   academic_low/high_annual_cost_usd = a sourced public range for a typical
   university research/academic licence, not necessarily this team''s own
   contract; commercial_annual_cost_usd = full retail, what a COMPANY would
   pay. `all_measured` is true only if EVERY feature baseline rolled into
   that row is confidence=measured rather than lead_estimate.';


-- ----------------------------------------------------------------------------
--  13. NOTE: v_individual_use.subteam fix — applied in section 5 above
-- ----------------------------------------------------------------------------
--  Reported symptom: a person known to have used the "brakes" role shows no
--  subteam (or "unknown") in the individual-use table. Root cause: the view
--  used `max(subteam)`, a TEXT aggregate returning the alphabetically-last
--  value. Every browser session starts as subteam='unknown' (the default,
--  logged on session_start and any events before the person picks a subteam),
--  and only later events carry the real subteam. Since 'unknown' sorts after
--  almost every real subteam name (aero, brakes, cooling, cost, electrics,
--  powertrain, suspension... all start before 'u'), `max()` systematically
--  PREFERRED 'unknown' and discarded the person's actual subteam. Not
--  brakes-specific — brakes was just one of many subteams being clobbered.
--
--  The fix (most-recent non-'unknown' subteam via array_agg) is applied
--  directly in the section 5 definition of v_individual_use above, rather
--  than as a second CREATE OR REPLACE here — keeping a single authoritative
--  definition of the view in this file avoids the multi-replace fragility
--  that previously caused a 42P16 error on re-run.


-- ----------------------------------------------------------------------------
--  14. RETURNING-USER RECOVERY — independent of the browser-side visitor_id
-- ----------------------------------------------------------------------------
--  Context: returning-user counts were stuck because the durable visitor_id
--  wasn't persisting across visits in production (a sandboxed-iframe /
--  cross-origin problem on the client). The app-side fix may or may not work
--  depending on whether the host exposes cookies/IP. These views give a
--  SQL-only answer that does NOT depend on visitor_id working at all, so you
--  get a trustworthy returning-user number from data already collected.
--
--  What's actually recoverable from the events table (no IP is stored, by
--  design, so IP-based identity cannot be reconstructed here):
--    * member — anyone who typed a name has FULLY durable cross-visit
--      identity already. This is the gold-standard signal.
--    * visitor_id — works only for the subset whose id did persist.
--  We expose retention at two confidence tiers so the board number is honest
--  about what's measured vs. degraded by the visitor_id churn.

-- 14a. How badly is visitor_id churning? If durable-id tracking is broken,
--      you'll see far more distinct visitor_ids than real people, and most
--      visitor_ids will have exactly one session. This quantifies the damage.
create or replace view v_visitor_id_health as
with vid_sessions as (
    select visitor_id,
           count(distinct session_id) as sessions,
           count(distinct occurred_at::date) as days
    from analytics_events
    where visitor_id is not null
    group by visitor_id
)
select
    count(*)                                          as distinct_visitor_ids,
    count(*) filter (where sessions = 1)              as single_session_vids,
    count(*) filter (where sessions >= 2)             as multi_session_vids,
    round(100.0 * count(*) filter (where sessions = 1)
          / nullif(count(*), 0), 1)                   as pct_single_session,
    -- a healthy durable id has many ids with repeat sessions; if
    -- pct_single_session is ~100, durable tracking is effectively broken.
    case
        when count(*) = 0 then 'no visitor_ids logged at all'
        when round(100.0 * count(*) filter (where sessions = 1)
                   / nullif(count(*), 0), 1) >= 95
            then 'BROKEN — nearly every visitor_id is single-session; durable id is not persisting'
        when round(100.0 * count(*) filter (where sessions = 1)
                   / nullif(count(*), 0), 1) >= 70
            then 'DEGRADED — most visitor_ids are single-session'
        else 'OK — meaningful share of visitor_ids span multiple sessions'
    end                                               as verdict
from vid_sessions;

comment on view v_visitor_id_health is
  'Diagnoses whether the durable visitor_id is actually persisting across
   visits. If pct_single_session is ~100, the browser-side id is broken and
   you should trust v_retention_recovered (member-based) instead of the
   visitor_id-based v_retention.';

-- 14b. Returning users by the MOST RELIABLE signal available, in tiers.
--      Tier 1 (named): member appears across 2+ distinct days — unambiguous.
--      Tier 2 (id):    visitor_id (for those it persisted) across 2+ sessions.
--      The named tier is the floor you can defend on a board slide regardless
--      of the visitor_id bug.
create or replace view v_retention_recovered as
with named as (
    select member as uid,
           count(distinct occurred_at::date) as active_days,
           count(distinct session_id)        as sessions
    from analytics_events
    where member is not null and member <> ''
    group by member
),
named_roll as (
    select
        count(*)                                  as named_users,
        count(*) filter (where sessions >= 2)     as named_returning,
        count(*) filter (where active_days >= 2)  as named_returning_distinct_days
    from named
),
vid AS (
    select visitor_id as uid,
           count(distinct session_id) as sessions
    from analytics_events
    where visitor_id is not null
      and member is null            -- avoid double-counting named users
    group by visitor_id
),
vid_roll as (
    select
        count(*)                              as anon_id_users,
        count(*) filter (where sessions >= 2) as anon_id_returning
    from vid
)
select
    n.named_users,
    n.named_returning,
    n.named_returning_distinct_days,
    v.anon_id_users,
    v.anon_id_returning,
    -- the DEFENSIBLE returning number: named users who came back on 2+ days.
    -- doesn't depend on visitor_id at all.
    n.named_returning_distinct_days                   as defensible_returning_users,
    -- best-effort total adding whatever anon visitor_ids did persist.
    n.named_returning_distinct_days + v.anon_id_returning
                                                      as best_effort_returning_users
from named_roll n cross join vid_roll v;

comment on view v_retention_recovered is
  'Returning-user counts that do NOT depend on the (possibly broken) durable
   visitor_id. defensible_returning_users = named people who returned on 2+
   distinct days — the number to quote when visitor_id health is BROKEN.
   best_effort_returning_users adds anonymous visitor_ids that did persist.
   Cross-check v_visitor_id_health to know which to trust.';


-- ----------------------------------------------------------------------------
--  15. SECURITY: clear "Security Definer View" warnings the RIGHT way
-- ----------------------------------------------------------------------------
--  Supabase flags views without security_invoker as "Security Definer View"
--  (CRITICAL) because they run with the OWNER's privileges and can bypass RLS.
--  The fix is security_invoker = on (run as the caller). The catch we hit:
--  invoker-mode needs the calling role to have SELECT on the underlying
--  analytics_events table, which it didn't — so views returned 42501
--  "permission denied" and the dashboard went blank.
--
--  Proper resolution (clears the warnings AND keeps the dashboard working):
--    1. GRANT SELECT on the base tables to the app roles. analytics_events
--       already has an OPEN read RLS policy (ae_read using(true)), so once the
--       role also has the table GRANT, invoker-mode reads succeed.
--    2. THEN set security_invoker = on on every view + grant select on the
--       views. Now each view runs as the caller, RLS is honoured, and the
--       linter is satisfied.
--  This fixes the actual root cause (missing base-table grant) rather than
--  working around it by leaving views in owner/definer mode. Idempotent.
do $$
declare
    _t text;
    _tables text[] := array[
        'analytics_events', 'feature_baselines', 'feature_releases',
        'analytics_config'
    ];
begin
    foreach _t in array _tables loop
        if exists (select 1 from information_schema.tables
                   where table_schema='public' and table_name=_t) then
            execute format('grant select on public.%I to anon, authenticated, service_role', _t);
        end if;
    end loop;
end $$;

do $$
declare
    _v text;
    _views text[] := array[
        'v_foot_traffic_daily', 'v_active_members_weekly', 'v_individual_use',
        'v_feature_use', 'v_feature_delivery', 'v_latency_by_feature',
        'v_error_rate', 'v_retention', 'v_time_to_first_result',
        'v_adoption_funnel', 'v_hours_saved_by_feature', 'v_roi_summary',
        'v_comparison_to_alternatives', 'v_instrumentation_coverage',
        'v_instrumentation_health', 'v_orphaned_feature_events',
        'v_visitor_id_health', 'v_retention_recovered'
    ];
begin
    foreach _v in array _views loop
        if exists (
            select 1 from information_schema.views
            where table_schema = 'public' and table_name = _v
        ) then
            -- base-table grant above lets invoker-mode reads succeed, so we can
            -- safely run views as the caller (clears the CRITICAL warning) AND
            -- grant select so the app role can reach the view itself.
            execute format('alter view public.%I set (security_invoker = on)', _v);
            execute format('grant select on public.%I to anon, authenticated, service_role', _v);
        end if;
    end loop;
end $$;


-- ----------------------------------------------------------------------------
--  16. RLS read policies — "RLS Policy Always True" warnings (JUDGEMENT CALL)
-- ----------------------------------------------------------------------------
--  The base schema created OPEN policies: `using (true)` for select and
--  `with check (true)` for insert on analytics_events (and open select on the
--  config/baseline/release tables). Supabase flags the always-true ones.
--
--  Honest assessment, NOT a blind auto-fix, because tightening wrong locks you
--  out of your own dashboard:
--   * INSERT open (with check (true)) is effectively REQUIRED — the app logs
--     events with no per-user login, so anonymous inserts must be allowed.
--     Leaving this open is a deliberate, reasonable choice for anonymous
--     telemetry. (You could narrow it to a specific API role if the app
--     authenticates as one.)
--   * SELECT open (using (true)) is the one worth tightening: it lets anyone
--     with your PUBLIC anon key read all usage data. If your dashboard reads
--     through the service_role key (which BYPASSES RLS anyway), you can safely
--     restrict public reads to authenticated users without breaking the
--     dashboard. Whether that's right depends on your Supabase setup, so it's
--     left COMMENTED below — uncomment only after confirming your dashboard
--     uses the service_role (not anon) key, or you'll lock yourself out.
--
-- -- Restrict reads to authenticated users (uncomment if dashboard uses
-- -- the service_role key, which bypasses RLS so the dashboard still works):
-- drop policy if exists ae_read  on analytics_events;
-- create policy ae_read  on analytics_events  for select
--     to authenticated using (true);
-- drop policy if exists fb_read  on feature_baselines;
-- create policy fb_read  on feature_baselines  for select
--     to authenticated using (true);
-- drop policy if exists fr_read  on feature_releases;
-- create policy fr_read  on feature_releases  for select
--     to authenticated using (true);
-- drop policy if exists cfg_read on analytics_config;
-- create policy cfg_read on analytics_config   for select
--     to authenticated using (true);


-- ----------------------------------------------------------------------------
--  17. FUNCTION warnings (log_workflow_completion, rls_auto_enable) — NOTE
-- ----------------------------------------------------------------------------
--  Supabase also flagged "Function Search Path Mutable" and "Public Can
--  Execute SECURITY DEFINER Function" on log_workflow_completion(...) and
--  rls_auto_enable(). Those functions are NOT defined in this repo's SQL
--  files — they were created directly in the Supabase dashboard/migrations —
--  so this script can't safely ALTER them without knowing their exact
--  signatures (guessing the arg types would error). Fix them where they're
--  defined with BOTH of:
--    1. pin the search_path:
--         alter function public.log_workflow_completion(<exact arg types>)
--             set search_path = public, pg_temp;
--         alter function public.rls_auto_enable() set search_path = public, pg_temp;
--       (a mutable search_path on a SECURITY DEFINER function is the real risk:
--        a caller could shadow objects the function references and run code as
--        the function owner.)
--    2. revoke public execute if these aren't meant to be called by anyone:
--         revoke execute on function public.rls_auto_enable() from public, anon;
--  Get the exact signatures from Supabase (Database -> Functions) or:
--    select oid::regprocedure from pg_proc
--    where proname in ('log_workflow_completion','rls_auto_enable');


-- ----------------------------------------------------------------------------
--  18. FIX returning-user UNDERCOUNT — empty-string member collapses identities
-- ----------------------------------------------------------------------------
--  Diagnosed from live data: raw returning users = 46, but v_retention reports
--  only 31. The data is RIGHT; the view undercounts by 15.
--
--  Root cause: identity is computed as coalesce(member, visitor_id, session_id).
--  coalesce only skips NULL — NOT empty string. When a user opens the app
--  without entering a name, member is logged as '' (empty string), not NULL.
--  So coalesce('', visitor_id, session_id) returns '' for EVERY such event, and
--  ALL blank-name users across ALL their sessions collapse into a SINGLE uid =
--  ''. That one giant ''-identity is counted as just ONE returning user, hiding
--  the ~15 distinct anonymous people who actually returned behind it.
--
--  Fix: nullif(member, '') turns a blank name into NULL so it correctly falls
--  through to the durable visitor_id. Applied to v_retention and to the other
--  views that key identity off member the same way. These are single,
--  authoritative redefinitions (v_retention isn't otherwise redefined in this
--  file), so no multi-replace fragility.

-- NOTE: drop-then-create (not CREATE OR REPLACE) because the LIVE view in the
-- database has a different column ORDER than this file (avg_active_days and
-- avg_visits_per_user are swapped — a sign the deployed view was made from a
-- different version). CREATE OR REPLACE can't reorder/rename columns (42P16),
-- so we drop and recreate. Safe: v_retention is a leaf view, nothing depends
-- on it.
drop view if exists v_retention cascade;
create view v_retention as
with
-- Phase 1: one row per session. Only fp- (fingerprint) and named-member
-- identities are durable across visits. ck- cookie ids are excluded —
-- cookie writes silently fail on this deployment so every session gets a
-- new ck- and they never link up.
per_session as (
    select
        session_id,
        coalesce(
            nullif(max(member), ''),
            max(visitor_id) filter (where visitor_id like 'fp-%')
        )                              as uid,
        min(occurred_at)::date         as session_date
    from analytics_events
    group by session_id
),
-- Phase 2: one row per identified person.
per_user as (
    select
        uid,
        count(distinct session_id)     as visits,
        count(distinct session_date)   as active_days,
        min(session_date)              as first_day,
        max(session_date)              as last_day
    from per_session
    where uid is not null
    group by uid
)
select
    (select count(distinct session_id) from analytics_events)  as total_users,
    -- return VISITS = all sessions beyond each person's first visit
    sum(greatest(visits - 1, 0))                               as returning_users,
    round(100.0 * sum(greatest(visits - 1, 0))
          / nullif((select count(distinct session_id) from analytics_events), 0), 1) as retention_pct,
    round(avg(visits), 2)                                      as avg_visits_per_user,
    round(avg(active_days), 2)                                 as avg_active_days,
    -- Live window bounds for the dashboard's cycle/window label. Read from the
    -- actual retained events (earliest/latest), so they move as the row/time cap
    -- trims old rows. Computed over all events, not just identified users.
    (select min(occurred_at)::date from analytics_events)      as window_start,
    (select max(occurred_at)::date from analytics_events)      as window_end
from per_user;

comment on view v_retention is
  'total_users = all distinct sessions ever. returning_users = total return
   VISITS (sessions beyond each person first visit), not distinct people.
   Identity uses fp- fingerprint and member name only — ck- excluded as
   cookie writes fail on this deployment.';

-- Same empty-string-member fix for the other two views that key identity off
-- member. Both also now include visitor_id in the identity chain (they
-- previously used only coalesce(member, session_id), ignoring the durable
-- visitor_id — which inflated unique/active counts by treating each visit as a
-- new person). With both fixes they count distinct PEOPLE correctly.
drop view if exists v_active_members_weekly cascade;
create view v_active_members_weekly as
select
    date_trunc('week', occurred_at)::date                         as week,
    count(distinct coalesce(nullif(member, ''), visitor_id, session_id)) as active_members,
    count(distinct session_id)                                    as sessions
from analytics_events
group by 1
order by 1;

drop view if exists v_feature_use cascade;
create view v_feature_use as
with per_feature_session as (
    -- one row per (feature, session): what's the DEEPEST stage this session
    -- reached? A session that engaged necessarily opened (even if the open
    -- event wasn't logged — the auto-running solvers log engage without a
    -- reliable open). Counting distinct SESSIONS (not raw events) also removes
    -- the double-fire inflation where tab_open fired ~2x in one session.
    select
        feature,
        session_id,
        coalesce(nullif(max(member), ''), max(visitor_id), session_id) as uid,
        bool_or(event_type = 'workflow_complete')                as completed,
        bool_or(event_type in ('feature_engage','workflow_complete')) as engaged,
        -- "opened" = did ANYTHING with the feature this session. Since engaging
        -- implies opening, every session here counts as an open, guaranteeing
        -- opens >= engagements >= completions.
        true                                                     as opened
    from analytics_events
    where feature is not null
    group by feature, session_id
)
select
    feature,
    count(*) filter (where opened)     as opens,        -- = sessions that touched it
    count(*) filter (where engaged)    as engagements,  -- subset: engaged or completed
    count(*) filter (where completed)  as completions,  -- subset: completed
    count(distinct uid)                as unique_users  -- distinct people (<= opens)
from per_feature_session
group by feature
order by engagements desc;

comment on view v_feature_use is
  'Per-feature usage as a TRUE FUNNEL, counted by distinct SESSIONS (not raw
   events, which double-counted when tab_open fired twice in one session).
   Each stage includes the deeper ones — a session that engaged is counted as
   having opened, since you cannot engage without opening even when the open
   event was not logged (the auto-running solvers log engage without a
   reliable tab_open). This guarantees opens >= engagements >= completions,
   and unique_users (distinct people) cannot exceed opens. Retroactive: it
   recomputes from raw events, so historical rows are corrected too.';

-- Re-apply security_invoker to the views that were DROPPED and recreated in
-- this section (drop loses the setting applied in section 15, which ran
-- earlier). Without this they'd re-trigger the CRITICAL "Security Definer
-- View" linter warning. Idempotent.
do $$
declare
    _v text;
    _recreated text[] := array[
        'v_retention', 'v_active_members_weekly', 'v_feature_use',
        'v_individual_use'
    ];
begin
    foreach _v in array _recreated loop
        if exists (select 1 from information_schema.views
                   where table_schema = 'public' and table_name = _v) then
            -- invoker ON + explicit grant, matching section 15. The base-table
            -- grants in section 15 make invoker-mode reads succeed, so this is
            -- safe and keeps the linter clean.
            execute format('alter view public.%I set (security_invoker = on)', _v);
            execute format('grant select on public.%I to anon, authenticated, service_role', _v);
        end if;
    end loop;
end $$;

-- =========================================================================== #
--  FIX v_time_to_first_result — anchor off first event not session_start      #
--  session_start is deferred to render 2 so first_result often fires before   #
--  it, producing negative deltas that average to a misleading "1 min".        #
-- =========================================================================== #
create or replace view v_time_to_first_result as
with starts as (
    select
        session_id,
        min(occurred_at)                                              as t_start,
        min(occurred_at) filter (
            where event_type in ('first_result', 'workflow_complete')
        )                                                             as t_first_result
    from analytics_events
    group by session_id
)
select
    count(*) filter (where t_first_result is not null)                as sessions_with_result,
    round(avg(
        extract(epoch from t_first_result - t_start) / 60.0
    )::numeric, 2)                                                    as avg_minutes_to_first_result,
    percentile_cont(0.5) within group (
        order by extract(epoch from t_first_result - t_start) / 60.0
    )                                                                 as median_minutes
from starts
where t_first_result is not null
  and t_first_result >= t_start;  -- exclude any remaining negatives from old data

