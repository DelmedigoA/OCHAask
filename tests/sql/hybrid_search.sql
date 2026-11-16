-- Run against an isolated database after applying all migrations.
begin;
set local role service_role;
do $$
declare
    rid uuid := '11111111-1111-1111-1111-111111111111';
    vector_a jsonb := to_jsonb(array_fill(0.0::real, array[1535]) || 1.0::real);
    vector_b jsonb := to_jsonb(array_fill(0.0::real, array[1535]) || (-1.0)::real);
    query_vector extensions.vector(1536) := (array_fill(0.0::real, array[1535]) || 1.0::real)::extensions.vector;
    report jsonb := jsonb_build_object('id', rid, 'landing_url', 'https://example.com', 'title', 'Test');
    chunks jsonb;
    found_count integer;
    first_id uuid;
    first_score double precision;
begin
    chunks := jsonb_build_array(
        jsonb_build_object('chunk_id', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'chunk_index', 0, 'content', 'water supply', 'pages', jsonb_build_array(1, 2), 'token_count', 2, 'embedding', vector_a),
        jsonb_build_object('chunk_id', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'chunk_index', 1, 'content', 'food supplies', 'pages', jsonb_build_array(3), 'token_count', 2, 'embedding', vector_b));
    perform public.replace_report_chunks(report, chunks);
    perform public.replace_report_chunks(report, chunks);
    select count(*) into found_count from public.report_embeddings where report_id = rid;
    assert found_count = 2, 'rerun must not duplicate chunks';
    select count(*) into found_count from public.hybrid_search_report_chunks('water', query_vector, 10, 1, 1);
    assert found_count = 2, 'semantic-only and overlapping results must be returned';
    select chunk_id, fusion_score into first_id, first_score from public.hybrid_search_report_chunks('water', query_vector, 1, 1, 1);
    assert first_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid and abs(first_score - 2.0/61.0) < 0.0000001, 'RRF overlap incorrect';
    select count(*) into found_count from public.hybrid_search_report_chunks('absentword', query_vector, 10, 0, 1);
    assert found_count = 0, 'empty keyword ranking';
    select chunk_id into first_id from public.hybrid_search_report_chunks('food', query_vector, 1, 0, 2);
    assert first_id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'::uuid, 'keyword-only weighting';
    begin
        perform public.replace_report_chunks(report, jsonb_build_array(jsonb_build_object('chunk_id', 'invalid')));
        raise exception 'invalid replacement unexpectedly succeeded';
    exception when invalid_text_representation then null;
    end;
    select count(*) into found_count from public.report_embeddings where report_id = rid;
    assert found_count = 2, 'failed replacement must roll back';
    perform public.replace_report_chunks(report, chunks - 1);
    select count(*) into found_count from public.report_embeddings where report_id = rid;
    assert found_count = 1, 'replacement must remove stale chunks';
    assert not has_function_privilege('anon', 'public.hybrid_search_report_chunks(text, extensions.vector, integer, double precision, double precision)', 'execute'), 'anon must not execute';
    assert has_function_privilege('service_role', 'public.hybrid_search_report_chunks(text, extensions.vector, integer, double precision, double precision)', 'execute'), 'service role must execute';
end;
$$;
rollback;
