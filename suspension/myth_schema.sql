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
