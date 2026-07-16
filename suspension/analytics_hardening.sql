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
