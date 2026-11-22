create or replace function public.claim_nominatim_lookup(p_query_key text, p_lease_id uuid)
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

    select available_at into next_request from public.nominatim_request_gate where singleton for update;

    -- A simultaneous lookup may have populated the cache before we got the lock.
    select places into cached from public.nominatim_cache where query_key = p_query_key;
    if found then
        return jsonb_build_object('places', cached);
    end if;
    if next_request > clock_timestamp() then
        return jsonb_build_object('retry_after', extract(epoch from next_request - clock_timestamp()));
    end if;

    update public.nominatim_request_gate
    set lease_id = p_lease_id, available_at = clock_timestamp() + interval '2 minutes'
    where singleton;
    return jsonb_build_object('claimed', true);
end;
$$;
