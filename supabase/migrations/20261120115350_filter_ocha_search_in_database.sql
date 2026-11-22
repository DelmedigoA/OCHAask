-- Rank filtered OCHA evidence in PostgreSQL and return only the requested passages.
create function public.hybrid_search_filtered_report_chunks(
    query_text text,
    query_embedding extensions.vector(1536),
    model_name text,
    match_count integer,
    semantic_weight double precision,
    keyword_weight double precision,
    report_date_from date,
    report_date_to date,
    report_issue integer,
    report_title text
)
returns table (
    chunk_id uuid, report_id uuid, title text, landing_url text, pages integer[],
    content text, fusion_score double precision
)
language sql stable security invoker set search_path = '' as $$
    with query_terms as materialized (
        select distinct word
        from regexp_split_to_table(lower(query_text), '[^[:alnum:]_]+') as term(word)
        where word <> ''
    ), candidates as materialized (
        select e.id, e.chunk_id, e.report_id, r.title, r.landing_url, e.pages, e.content,
            e.embedding operator(extensions.<=>) query_embedding as distance,
            case when keyword_weight > 0 then (
                select count(*) from query_terms q
                where q.word = any(regexp_split_to_array(lower(e.content), '[^[:alnum:]_]+'))
            ) else 0 end as word_matches
        from public.report_embeddings e
        join public.report_landings r on r.id = e.report_id
        where e.embedding_model = model_name and e.chunk_id is not null and e.embedding is not null
          and (report_date_from is null or coalesce(r.originally_published, r.posted) >= report_date_from)
          and (report_date_to is null or coalesce(r.originally_published, r.posted) <= report_date_to)
          and (report_issue is null or r.title ~* (E'(#|\\mno\\.?)\\s*' || report_issue || E'\\M'))
          and (report_title is null or strpos(lower(r.title), lower(report_title)) > 0)
    ), ranked as (
        select *,
            row_number() over (order by distance, id) as semantic_rank,
            row_number() over (order by word_matches desc, id) as keyword_rank
        from candidates
    ), scored as (
        select *, semantic_weight / (60 + semantic_rank)
            + case when word_matches > 0 then keyword_weight / (60 + keyword_rank) else 0 end
            as score
        from ranked
    )
    select s.chunk_id, s.report_id, s.title, s.landing_url, s.pages, s.content, s.score
    from scored s
    order by s.score desc, s.id
    limit least(greatest(match_count, 1), 100);
$$;

revoke all on function public.hybrid_search_filtered_report_chunks(
    text, extensions.vector, text, integer, double precision, double precision, date, date, integer, text
) from public, anon, authenticated;
grant execute on function public.hybrid_search_filtered_report_chunks(
    text, extensions.vector, text, integer, double precision, double precision, date, date, integer, text
) to service_role;
