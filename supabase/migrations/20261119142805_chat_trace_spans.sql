create table public.ochask_trace_spans (
    trace_id text not null check (trace_id ~ '^[0-9a-f]{32}$'),
    span_id text not null check (span_id ~ '^[0-9a-f]{16}$'),
    parent_span_id text check (parent_span_id ~ '^[0-9a-f]{16}$'),
    span_name text not null,
    start_timestamp timestamptz not null,
    end_timestamp timestamptz not null,
    status jsonb not null,
    attributes jsonb not null,
    events jsonb not null,
    environment text,
    primary key (trace_id, span_id)
);

create index ochask_trace_spans_recent_chat
    on public.ochask_trace_spans (start_timestamp desc)
    where span_name = 'chat request' and parent_span_id is null;

alter table public.ochask_trace_spans enable row level security;
revoke all on public.ochask_trace_spans from public, anon, authenticated;
grant select, insert, delete on public.ochask_trace_spans to service_role;
