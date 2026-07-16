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
