create table public.nominatim_cache (
    query_key text primary key,
    places jsonb not null
);

create table public.nominatim_request_gate (
    singleton boolean primary key default true check (singleton),
    lease_id uuid,
    available_at timestamptz not null default '-infinity'
);

insert into public.nominatim_request_gate default values;

alter table public.nominatim_cache enable row level security;
alter table public.nominatim_request_gate enable row level security;
revoke all on public.nominatim_cache, public.nominatim_request_gate from public, anon, authenticated;
grant select, insert, update on public.nominatim_cache, public.nominatim_request_gate to service_role;

create function public.claim_nominatim_lookup(p_query_key text, p_lease_id uuid)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
    cached jsonb;
    next_request timestamptz;
begin
    select places into cached from public.nominatim_cache where query_key = p_query_key;
    if found then
        return jsonb_build_object('places', cached);
    end if;

    select available_at into next_request from public.nominatim_request_gate for update;

    -- A simultaneous lookup may have populated the cache before we got the lock.
    select places into cached from public.nominatim_cache where query_key = p_query_key;
    if found then
        return jsonb_build_object('places', cached);
    end if;
    if next_request > clock_timestamp() then
        return jsonb_build_object('retry_after', extract(epoch from next_request - clock_timestamp()));
    end if;

    update public.nominatim_request_gate
    set lease_id = p_lease_id, available_at = clock_timestamp() + interval '2 minutes';
    return jsonb_build_object('claimed', true);
end;
$$;

create function public.finish_nominatim_lookup(p_query_key text, p_lease_id uuid, p_places jsonb)
returns void
language plpgsql
set search_path = ''
as $$
begin
    update public.nominatim_request_gate
    set lease_id = null, available_at = clock_timestamp() + interval '1 second'
    where lease_id = p_lease_id;
    if found and p_places is not null then
        insert into public.nominatim_cache (query_key, places) values (p_query_key, p_places)
        on conflict (query_key) do update set places = excluded.places;
    end if;
end;
$$;

revoke all on function public.claim_nominatim_lookup(text, uuid),
    public.finish_nominatim_lookup(text, uuid, jsonb) from public, anon, authenticated;
grant execute on function public.claim_nominatim_lookup(text, uuid),
    public.finish_nominatim_lookup(text, uuid, jsonb) to service_role;
