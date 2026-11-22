-- Increase monthly allowances while preserving usage and pending reservations.
create or replace function public.bw_get_chat_account(p_visitor_hash text)
returns table (
    provider_mode text,
    openai_key_configured boolean,
    input_used bigint,
    output_used bigint,
    input_remaining bigint,
    output_remaining bigint,
    month_start date
)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_month date := date_trunc('month', timezone('UTC', now()))::date;
begin
    if p_visitor_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Invalid browser identity';
    end if;

    insert into public.bw_browser_profiles (visitor_hash)
    values (p_visitor_hash)
    on conflict (visitor_hash) do nothing;

    insert into public.bw_browser_monthly_usage (visitor_hash, month_start)
    values (p_visitor_hash, v_month)
    on conflict on constraint bw_browser_monthly_usage_pkey do nothing;

    return query
    select p.provider_mode,
           p.openai_key_ciphertext is not null,
           u.input_tokens,
           u.output_tokens,
           greatest(5000000::bigint - u.input_tokens, 0),
           greatest(512000::bigint - u.output_tokens, 0),
           v_month
    from public.bw_browser_profiles p
    join public.bw_browser_monthly_usage u
      on u.visitor_hash = p.visitor_hash and u.month_start = v_month
    where p.visitor_hash = p_visitor_hash;
end;
$$;

create or replace function public.bw_reserve_chat_usage(
    p_visitor_hash text,
    p_run_id uuid,
    p_input_limit bigint,
    p_output_limit bigint
)
returns table (
    allowed boolean,
    input_limit bigint,
    output_limit bigint,
    input_used bigint,
    output_used bigint,
    input_remaining bigint,
    output_remaining bigint,
    month_start date
)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_month date := date_trunc('month', timezone('UTC', now()))::date;
    v_used public.bw_browser_monthly_usage%rowtype;
    v_old public.bw_browser_usage_reservations%rowtype;
    v_reserved_input bigint;
    v_reserved_output bigint;
    v_expired_input bigint;
    v_expired_output bigint;
    v_input_limit bigint;
    v_output_limit bigint;
begin
    if p_visitor_hash !~ '^[0-9a-f]{64}$' or p_run_id is null
       or p_input_limit <= 0 or p_output_limit <= 0 then
        raise exception 'Invalid quota reservation';
    end if;

    insert into public.bw_browser_profiles (visitor_hash)
    values (p_visitor_hash)
    on conflict (visitor_hash) do nothing;
    insert into public.bw_browser_monthly_usage (visitor_hash, month_start)
    values (p_visitor_hash, v_month)
    on conflict on constraint bw_browser_monthly_usage_pkey do nothing;

    select * into v_used
    from public.bw_browser_monthly_usage u
    where u.visitor_hash = p_visitor_hash and u.month_start = v_month
    for update;

    select coalesce(sum(r.input_reserved), 0), coalesce(sum(r.output_reserved), 0)
      into v_expired_input, v_expired_output
    from public.bw_browser_usage_reservations r
    where r.visitor_hash = p_visitor_hash and r.month_start = v_month
      and r.expires_at <= now();

    if v_expired_input > 0 or v_expired_output > 0 then
        update public.bw_browser_monthly_usage u
        set input_tokens = u.input_tokens + v_expired_input,
            output_tokens = u.output_tokens + v_expired_output,
            updated_at = now()
        where u.visitor_hash = p_visitor_hash and u.month_start = v_month
        returning * into v_used;

        delete from public.bw_browser_usage_reservations r
        where r.visitor_hash = p_visitor_hash and r.month_start = v_month
          and r.expires_at <= now();
    end if;

    select * into v_old
    from public.bw_browser_usage_reservations r
    where r.visitor_hash = p_visitor_hash and r.month_start = v_month and r.run_id = p_run_id;

    if found then
        v_input_limit := v_old.input_reserved;
        v_output_limit := v_old.output_reserved;
    else
        select coalesce(sum(input_reserved), 0), coalesce(sum(output_reserved), 0)
          into v_reserved_input, v_reserved_output
        from public.bw_browser_usage_reservations r
        where r.visitor_hash = p_visitor_hash and r.month_start = v_month;

        v_input_limit := least(p_input_limit,
            greatest(5000000::bigint - v_used.input_tokens - v_reserved_input, 0));
        v_output_limit := least(p_output_limit,
            greatest(512000::bigint - v_used.output_tokens - v_reserved_output, 0));

        if v_input_limit > 0 and v_output_limit > 0 then
            insert into public.bw_browser_usage_reservations
                (visitor_hash, month_start, run_id, input_reserved, output_reserved, expires_at)
            values
                (p_visitor_hash, v_month, p_run_id, v_input_limit, v_output_limit, now() + interval '15 minutes');
        end if;
    end if;

    return query select
        v_input_limit > 0 and v_output_limit > 0,
        greatest(v_input_limit, 0), greatest(v_output_limit, 0),
        v_used.input_tokens, v_used.output_tokens,
        greatest(5000000::bigint - v_used.input_tokens, 0),
        greatest(512000::bigint - v_used.output_tokens, 0),
        v_month;
end;
$$;

create or replace function public.bw_settle_chat_usage(
    p_visitor_hash text,
    p_run_id uuid,
    p_input_tokens bigint,
    p_output_tokens bigint
)
returns table (
    settled boolean,
    input_used bigint,
    output_used bigint,
    input_remaining bigint,
    output_remaining bigint,
    month_start date
)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_month date := date_trunc('month', timezone('UTC', now()))::date;
    v_reservation public.bw_browser_usage_reservations%rowtype;
    v_usage public.bw_browser_monthly_usage%rowtype;
begin
    select * into v_usage
    from public.bw_browser_monthly_usage u
    where u.visitor_hash = p_visitor_hash and u.month_start = v_month
    for update;

    select * into v_reservation
    from public.bw_browser_usage_reservations r
    where r.visitor_hash = p_visitor_hash and r.month_start = v_month and r.run_id = p_run_id
    for update;

    if not found then
        return query select false, v_usage.input_tokens, v_usage.output_tokens,
            greatest(5000000::bigint - v_usage.input_tokens, 0),
            greatest(512000::bigint - v_usage.output_tokens, 0), v_month;
        return;
    end if;

    delete from public.bw_browser_usage_reservations r
    where r.visitor_hash = p_visitor_hash and r.month_start = v_month and r.run_id = p_run_id;

    update public.bw_browser_monthly_usage
    set input_tokens = input_tokens + greatest(coalesce(p_input_tokens, 0), 0),
        output_tokens = output_tokens + greatest(coalesce(p_output_tokens, 0), 0),
        updated_at = now()
    where bw_browser_monthly_usage.visitor_hash = p_visitor_hash
      and bw_browser_monthly_usage.month_start = v_month
    returning * into v_usage;

    return query select true, v_usage.input_tokens, v_usage.output_tokens,
        greatest(5000000::bigint - v_usage.input_tokens, 0),
        greatest(512000::bigint - v_usage.output_tokens, 0), v_month;
end;
$$;
