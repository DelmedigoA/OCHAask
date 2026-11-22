-- Use the existing full-text index rather than splitting the full passage text for each search term.
create or replace function public.hybrid_search_filtered_report_chunks(
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
    with eligible_reports as materialized (
        select r.id, r.title, r.landing_url
        from public.report_landings r
        where (report_date_from is null or coalesce(r.originally_published, r.posted) >= report_date_from)
          and (report_date_to is null or coalesce(r.originally_published, r.posted) <= report_date_to)
          and (report_issue is null or r.title ~* (E'(#|\\mno\\.?)\\s*' || report_issue || E'\\M'))
          and (report_title is null or strpos(lower(r.title), lower(report_title)) > 0)
    ), terms as (
        select coalesce(string_agg(quote_literal(word), ' | '), '')::tsquery as query
        from unnest(tsvector_to_array(to_tsvector('english'::regconfig, query_text))) as term(word)
    ), semantic_candidates as materialized (
        select e.id, e.embedding operator(extensions.<=>) query_embedding as distance
        from public.report_embeddings e join eligible_reports r on r.id = e.report_id
        where e.embedding_model = model_name and e.chunk_id is not null and e.embedding is not null
          and semantic_weight > 0
        order by distance, e.id
        limit least(greatest(match_count, 1), 100) * 10
    ), semantic as (
        select id, row_number() over (order by distance, id) as rank from semantic_candidates
    ), keyword_candidates as materialized (
        select e.id, ts_rank_cd(e.content_search, t.query) as relevance
        from terms t cross join public.report_embeddings e
        join eligible_reports r on r.id = e.report_id
        where e.embedding_model = model_name and e.chunk_id is not null and e.embedding is not null
          and keyword_weight > 0 and e.content_search @@ t.query
        order by relevance desc, e.id
        limit least(greatest(match_count, 1), 100) * 10
    ), keywords as (
        select id, row_number() over (order by relevance desc, id) as rank from keyword_candidates
    ), fused as (
        select coalesce(s.id, k.id) as id,
            coalesce(semantic_weight / (60.0 + s.rank), 0)
                + coalesce(keyword_weight / (60.0 + k.rank), 0) as score
        from semantic s full outer join keywords k on s.id = k.id
    )
    select e.chunk_id, e.report_id, r.title, r.landing_url, e.pages, e.content, f.score
    from fused f join public.report_embeddings e on e.id = f.id
    join eligible_reports r on r.id = e.report_id
    order by f.score desc, e.id
    limit least(greatest(match_count, 1), 100);
$$;
