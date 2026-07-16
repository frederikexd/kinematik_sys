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
