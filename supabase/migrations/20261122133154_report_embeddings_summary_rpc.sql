create or replace function public.summarize_report_embeddings(model_name text)
returns table (
    report_id uuid,
    chunk_token_count bigint,
    token_count_complete boolean
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        e.report_id,
        coalesce(
            sum(case when e.token_count >= 0 then e.token_count else 0 end),
            0
        )::bigint as chunk_token_count,
        bool_and(e.token_count is not null and e.token_count >= 0)
            as token_count_complete
    from public.report_embeddings as e
    where e.embedding_model = model_name
      and e.chunk_id is not null
    group by e.report_id;
$$;

revoke all on function public.summarize_report_embeddings(text)
    from public, anon, authenticated;
grant execute on function public.summarize_report_embeddings(text)
    to service_role;
