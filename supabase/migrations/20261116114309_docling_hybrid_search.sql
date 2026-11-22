-- Keep old vectors until their report is successfully reindexed.
alter table public.report_embeddings
    add column if not exists chunk_id uuid,
    add column if not exists pages integer[] not null default '{}',
    add column if not exists token_count integer,
    add column if not exists content_search tsvector
        generated always as (to_tsvector('english'::regconfig, content)) stored;

create index if not exists report_embeddings_content_search_idx
    on public.report_embeddings using gin (content_search);
create unique index if not exists report_embeddings_chunk_id_idx
    on public.report_embeddings (chunk_id, embedding_model) where chunk_id is not null;

grant usage on schema extensions to service_role;
grant select, insert, update on public.report_landings to service_role;

-- All writes for a report happen in one transaction, including metadata.
create or replace function public.replace_report_chunks(report_data jsonb, chunks_data jsonb, model_name text)
returns void language plpgsql security invoker set search_path = '' as $$
declare
    target_id uuid := (report_data->>'id')::uuid;
begin
    if target_id is null or nullif(trim(model_name), '') is null or jsonb_typeof(chunks_data) <> 'array' or jsonb_array_length(chunks_data) = 0 then
        raise exception 'A report ID and non-empty chunks are required';
    end if;
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(target_id::text, 0));
    insert into public.report_landings (id, landing_url, title, report_type, posted, originally_published,
        origin_url, main_image_url, main_image_credit, key_points, metadata)
    values (target_id, report_data->>'landing_url', report_data->>'title', report_data->>'report_type',
        (report_data->>'posted')::date, (report_data->>'originally_published')::date,
        report_data->>'origin_url', report_data->>'main_image_url', report_data->>'main_image_credit',
        array(select jsonb_array_elements_text(coalesce(report_data->'key_points', '[]'::jsonb))),
        coalesce(report_data->'metadata', '{}'::jsonb))
    on conflict (id) do update set landing_url = excluded.landing_url, title = excluded.title,
        report_type = excluded.report_type, posted = excluded.posted,
        originally_published = excluded.originally_published, origin_url = excluded.origin_url,
        main_image_url = excluded.main_image_url, main_image_credit = excluded.main_image_credit,
        key_points = excluded.key_points, metadata = excluded.metadata, updated_at = now();

    delete from public.report_embeddings where report_id = target_id and embedding_model = model_name;
    insert into public.report_embeddings (report_id, chunk_id, chunk_index, content, pages, token_count, embedding, embedding_model)
    select target_id, (c->>'chunk_id')::uuid, (c->>'chunk_index')::integer, c->>'content',
        array(select jsonb_array_elements_text(coalesce(c->'pages', '[]'::jsonb))::integer),
        (c->>'token_count')::integer, (c->>'embedding')::extensions.vector(1536), model_name
    from jsonb_array_elements(chunks_data) as c;
end;
$$;

revoke all on function public.replace_report_chunks(jsonb, jsonb, text) from public, anon, authenticated;
grant execute on function public.replace_report_chunks(jsonb, jsonb, text) to service_role;

create or replace function public.hybrid_search_report_chunks(
    query_text text, query_embedding extensions.vector(1536), model_name text, match_count integer default 10,
    semantic_weight double precision default 1, keyword_weight double precision default 1)
returns table (chunk_id uuid, report_id uuid, title text, landing_url text, pages integer[],
               content text, fusion_score double precision)
language sql stable security invoker set search_path = '' as $$
    with semantic_candidates as materialized (
        select e.id, e.embedding operator(extensions.<=>) query_embedding as distance
        from public.report_embeddings e
        where e.embedding is not null and e.embedding_model = model_name and e.chunk_id is not null
          and semantic_weight > 0
        order by e.embedding operator(extensions.<=>) query_embedding
        limit least(greatest(match_count, 1), 100) * 10
    ), semantic as (
        select id, row_number() over (order by distance, id) as rank from semantic_candidates
    ), keywords as (
        select e.id, row_number() over (order by ts_rank_cd(e.content_search, websearch_to_tsquery('english'::regconfig, query_text)) desc, e.id) as rank
        from public.report_embeddings e
        where e.chunk_id is not null and e.embedding_model = model_name and keyword_weight > 0
          and e.content_search @@ websearch_to_tsquery('english'::regconfig, query_text)
        order by ts_rank_cd(e.content_search, websearch_to_tsquery('english'::regconfig, query_text)) desc, e.id
        limit least(greatest(match_count, 1), 100) * 10
    ), fused as (
        select coalesce(s.id, k.id) as id,
            coalesce(semantic_weight / (60.0 + s.rank), 0) + coalesce(keyword_weight / (60.0 + k.rank), 0) as score
        from semantic s full outer join keywords k on s.id = k.id
    )
    select e.chunk_id, e.report_id, r.title, r.landing_url, e.pages, e.content, f.score
    from fused f join public.report_embeddings e on e.id = f.id
    join public.report_landings r on r.id = e.report_id
    order by f.score desc, e.id
    limit least(greatest(match_count, 1), 100);
$$;

revoke all on function public.hybrid_search_report_chunks(text, extensions.vector, text, integer, double precision, double precision) from public, anon, authenticated;
grant execute on function public.hybrid_search_report_chunks(text, extensions.vector, text, integer, double precision, double precision) to service_role;
