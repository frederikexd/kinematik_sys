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
