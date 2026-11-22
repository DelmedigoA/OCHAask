-- Per-browser monthly usage and encrypted OpenAI credentials.
-- These tables are service-role only; the browser identity is never stored raw.
create table public.bw_browser_profiles (
    visitor_hash text primary key check (visitor_hash ~ '^[0-9a-f]{64}$'),
    provider_mode text not null default 'platform'
        check (provider_mode in ('platform', 'byok')),
    openai_key_nonce text,
    openai_key_ciphertext text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check ((openai_key_nonce is null) = (openai_key_ciphertext is null))
);

create table public.bw_browser_monthly_usage (
    visitor_hash text not null references public.bw_browser_profiles(visitor_hash) on delete cascade,
    month_start date not null,
    input_tokens bigint not null default 0 check (input_tokens >= 0),
    output_tokens bigint not null default 0 check (output_tokens >= 0),
    updated_at timestamptz not null default now(),
    primary key (visitor_hash, month_start)
);

create table public.bw_browser_usage_reservations (
    visitor_hash text not null,
    month_start date not null,
    run_id uuid not null,
    input_reserved bigint not null check (input_reserved > 0),
    output_reserved bigint not null check (output_reserved > 0),
    expires_at timestamptz not null,
    primary key (visitor_hash, month_start, run_id),
    foreign key (visitor_hash, month_start)
        references public.bw_browser_monthly_usage(visitor_hash, month_start)
        on delete cascade
);

create index bw_browser_usage_reservations_expiry_idx
    on public.bw_browser_usage_reservations (expires_at);

alter table public.bw_browser_profiles enable row level security;
alter table public.bw_browser_monthly_usage enable row level security;
alter table public.bw_browser_usage_reservations enable row level security;

revoke all on public.bw_browser_profiles from public, anon, authenticated;
revoke all on public.bw_browser_monthly_usage from public, anon, authenticated;
revoke all on public.bw_browser_usage_reservations from public, anon, authenticated;
grant all on public.bw_browser_profiles to service_role;
grant all on public.bw_browser_monthly_usage to service_role;
grant all on public.bw_browser_usage_reservations to service_role;

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
           greatest(1000000::bigint - u.input_tokens, 0),
           greatest(128000::bigint - u.output_tokens, 0),
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
            greatest(1000000::bigint - v_used.input_tokens - v_reserved_input, 0));
        v_output_limit := least(p_output_limit,
            greatest(128000::bigint - v_used.output_tokens - v_reserved_output, 0));

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
        greatest(1000000::bigint - v_used.input_tokens, 0),
        greatest(128000::bigint - v_used.output_tokens, 0),
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
            greatest(1000000::bigint - v_usage.input_tokens, 0),
            greatest(128000::bigint - v_usage.output_tokens, 0), v_month;
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
        greatest(1000000::bigint - v_usage.input_tokens, 0),
        greatest(128000::bigint - v_usage.output_tokens, 0), v_month;
end;
$$;

create or replace function public.bw_set_openai_key(
    p_visitor_hash text,
    p_key_nonce text,
    p_key_ciphertext text
)
returns table (provider_mode text, openai_key_configured boolean)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
    if p_visitor_hash !~ '^[0-9a-f]{64}$' or coalesce(p_key_nonce, '') = ''
       or coalesce(p_key_ciphertext, '') = '' then
        raise exception 'Invalid encrypted key';
    end if;

    insert into public.bw_browser_profiles
        (visitor_hash, provider_mode, openai_key_nonce, openai_key_ciphertext, updated_at)
    values (p_visitor_hash, 'byok', p_key_nonce, p_key_ciphertext, now())
    on conflict (visitor_hash) do update
      set provider_mode = 'byok',
          openai_key_nonce = excluded.openai_key_nonce,
          openai_key_ciphertext = excluded.openai_key_ciphertext,
          updated_at = now();

    return query select p.provider_mode, p.openai_key_ciphertext is not null
    from public.bw_browser_profiles p where p.visitor_hash = p_visitor_hash;
end;
$$;

create or replace function public.bw_remove_openai_key(p_visitor_hash text)
returns table (provider_mode text, openai_key_configured boolean)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
    if p_visitor_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Invalid browser identity';
    end if;

    insert into public.bw_browser_profiles (visitor_hash)
    values (p_visitor_hash)
    on conflict (visitor_hash) do nothing;

    update public.bw_browser_profiles
    set provider_mode = 'platform', openai_key_nonce = null,
        openai_key_ciphertext = null, updated_at = now()
    where visitor_hash = p_visitor_hash;

    return query select p.provider_mode, p.openai_key_ciphertext is not null
    from public.bw_browser_profiles p where p.visitor_hash = p_visitor_hash;
end;
$$;

create or replace function public.bw_set_provider_mode(p_visitor_hash text, p_mode text)
returns table (provider_mode text, openai_key_configured boolean)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_configured boolean;
begin
    if p_visitor_hash !~ '^[0-9a-f]{64}$' or p_mode not in ('platform', 'byok') then
        raise exception 'Invalid provider mode';
    end if;

    insert into public.bw_browser_profiles (visitor_hash)
    values (p_visitor_hash)
    on conflict (visitor_hash) do nothing;

    select openai_key_ciphertext is not null into v_configured
    from public.bw_browser_profiles where visitor_hash = p_visitor_hash for update;

    if p_mode = 'byok' and not v_configured then
        raise exception 'OpenAI key is not configured';
    end if;

    update public.bw_browser_profiles
    set provider_mode = p_mode, updated_at = now()
    where visitor_hash = p_visitor_hash;

    return query select p.provider_mode, p.openai_key_ciphertext is not null
    from public.bw_browser_profiles p where p.visitor_hash = p_visitor_hash;
end;
$$;

revoke all on function public.bw_get_chat_account(text) from public, anon, authenticated;
revoke all on function public.bw_reserve_chat_usage(text, uuid, bigint, bigint) from public, anon, authenticated;
revoke all on function public.bw_settle_chat_usage(text, uuid, bigint, bigint) from public, anon, authenticated;
revoke all on function public.bw_set_openai_key(text, text, text) from public, anon, authenticated;
revoke all on function public.bw_remove_openai_key(text) from public, anon, authenticated;
revoke all on function public.bw_set_provider_mode(text, text) from public, anon, authenticated;

grant execute on function public.bw_get_chat_account(text) to service_role;
grant execute on function public.bw_reserve_chat_usage(text, uuid, bigint, bigint) to service_role;
grant execute on function public.bw_settle_chat_usage(text, uuid, bigint, bigint) to service_role;
grant execute on function public.bw_set_openai_key(text, text, text) to service_role;
grant execute on function public.bw_remove_openai_key(text) to service_role;
grant execute on function public.bw_set_provider_mode(text, text) to service_role;
