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
