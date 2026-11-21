import importlib
import json
from uuid import uuid4

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage
from starlette.testclient import TestClient


class StreamingModel(FunctionModel):
    async def count_tokens(self, messages, model_settings, model_request_parameters):
        return RequestUsage(input_tokens=100)


@pytest.fixture
def chat(monkeypatch):
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "openai:gpt-4.1-mini")
    monkeypatch.delenv("OCHASK_ARCHIVE_MCP_URL", raising=False)
    chat = importlib.import_module("ochask.chat")

    class AllowGuard:
        async def check(self, _question, _recent_context=None, _enabled_sources=None):
            return True, False

    monkeypatch.setattr(chat, "relevance_guard", AllowGuard())
    return chat


def test_search_tool_requests_up_to_30_chunks(chat, monkeypatch):
    calls = []

    def fake_search(query, *, limit):
        calls.append((query, limit))
        return []

    monkeypatch.setattr(chat, "search", fake_search)
    assert chat.search_reports("water") == []
    assert calls == [("water", 30)]


def test_zero_result_filtered_search_reports_corpus_coverage(chat, monkeypatch):
    from ochask.web.app import app
    from pydantic_ai.messages import ToolReturnPart
    from ochask.config import Settings

    monkeypatch.setattr(chat, "search", lambda query, **kwargs: [])
    monkeypatch.setattr(Settings, "from_environment", lambda: Settings("https://example.test", "key", "model"))
    reads = []

    def read_rows(_url, _key, table, *, select, filters):
        reads.append((table, select, filters))
        return [
            {"originally_published": "2023-10-07", "posted": "2023-10-08"},
            {"originally_published": None, "posted": "2026-10-05T10:00:00+00:00"},
        ]

    monkeypatch.setattr(chat.tools, "rest_rows", read_rows)
    returns = []

    async def respond(messages, _info):
        results = [part.content for message in messages for part in message.parts
                   if isinstance(part, ToolReturnPart)]
        if results:
            returns.extend(results)
            yield "Outside the corpus."
        else:
            yield {0: DeltaToolCall(
                name="search_ocha_corpus",
                json_args='{"query":"trucks","date_from":"2026-11-12","date_to":"2026-11-25"}',
            )}

    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            stream_events(post_run(
                client,
                [{"id": "question", "role": "user", "content": "Trucks in November 2026?"}],
                {"sources": {"archive": False, "ocha": True}},
            ))

    assert returns
    payload = returns[-1]
    assert payload["results"] == []
    assert payload["corpus_coverage"] == {"publication_start": "2023-10-07", "publication_end": "2026-10-05"}
    assert "outside the indexed corpus" in payload["note"]
    assert len(reads) == 1
    table, select, filters = reads[0]
    assert table == "report_landings"
    assert "report_embeddings!inner()" in select
    assert filters["report_embeddings.embedding_model"] == "eq.model"
    assert filters["report_embeddings.chunk_id"] == filters["report_embeddings.embedding"] == "not.is.null"


def test_agent_has_bearing_witness_mcp_toolset(chat):
    from pydantic_ai.mcp import MCPToolset

    assert isinstance(chat.archive_toolset.wrapped, MCPToolset)
    assert chat.archive_toolset.wrapped.include_instructions is False
    assert chat.archive_mcp_url == "https://archiveai.bw-team.com/mcp"


@pytest.mark.parametrize("failure", ["connect", "discovery"])
def test_archive_mcp_failure_disables_archive_tools_without_raising(chat, failure):
    import asyncio

    class FailingToolset:
        async def __aenter__(self):
            if failure == "connect":
                raise RuntimeError("offline")
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_tools(self, _ctx):
            if failure == "discovery":
                raise RuntimeError("invalid tool list")
            raise AssertionError("tool discovery should be skipped after connection failure")

    optional = chat.OptionalArchiveToolset(FailingToolset())

    async def check():
        await optional.__aenter__()
        assert await optional.get_tools(None) == {}
        await optional.__aexit__(None, None, None)

    asyncio.run(check())


def test_archive_mcp_url_can_be_configured(monkeypatch):
    monkeypatch.setenv("OCHASK_ARCHIVE_MCP_URL", "https://archive.example/mcp")
    chat = importlib.import_module("ochask.chat")
    assert chat.archive_mcp_endpoint() == "https://archive.example/mcp"


def test_chat_ui_endpoint(chat):
    from ochask.web.app import app

    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/").status_code == 200


def post_run(client, messages, settings=None):
    payload = {
        "threadId": str(uuid4()),
        "runId": str(uuid4()),
        "messages": messages,
    }
    if settings is not None:
        payload["forwardedProps"] = {"ochask": settings}
    return client.post("/api/chat", json=payload, headers={"Accept": "text/event-stream"})


def stream_events(response):
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert not [event for event in events if event["type"] == "RUN_ERROR"], events
    assert events[-1]["type"] == "RUN_FINISHED"
    return events


@pytest.mark.parametrize(
    ("unavailable", "message"),
    [
        (False, "I can help with OCHA humanitarian reports"),
        (True, "I can’t check that request right now"),
    ],
)
def test_relevance_guard_marks_only_scope_refusals(chat, monkeypatch, unavailable, message):
    from ochask.web.app import app
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    class RejectingGuard:
        async def check(self, _question, _recent_context=None, _enabled_sources=None):
            return False, unavailable

    monkeypatch.setattr(chat, "relevance_guard", RejectingGuard())

    def should_not_run_agent(*_args, **_kwargs):
        raise AssertionError("Out-of-scope request reached the answering model")

    monkeypatch.setattr(AGUIAdapter, "run_stream", should_not_run_agent)
    with TestClient(app, base_url="http://localhost") as client:
        events = stream_events(post_run(
            client,
            [{"id": "question", "role": "user", "content": "Tell me a joke"}],
        ))

    assert [event["type"] for event in events] == [
        "RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED",
    ]
    content = next(event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT")
    assert content.startswith(message)
    snapshot = next(event["snapshot"] for event in events if event["type"] == "STATE_SNAPSHOT")
    assert snapshot["ochaskSourceContext"]["sources"] == []
    assert snapshot["ochaskGuardrailMessageId"] == (None if unavailable else events[1]["messageId"])


@pytest.mark.parametrize(
    ("archive", "ocha", "offline"),
    [(True, True, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_source_toggles_control_prompt_and_toolset(chat, monkeypatch, archive, ocha, offline):
    from ochask.web.app import app

    entered = []
    invocations = []

    class ArchiveToolset(FunctionToolset):
        async def __aenter__(self):
            entered.append(True)
            if offline:
                raise RuntimeError("offline")
            return self

    def search_archive(query: str) -> list:
        """Search the test archive."""
        return []

    fake_archive = chat.OptionalArchiveToolset(ArchiveToolset([search_archive]))
    monkeypatch.setattr(chat, "archive_toolset", fake_archive)

    async def respond(messages, info):
        invocations.append(info)
        yield "Test answer"

    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            response = post_run(
                client, [{"id": "question", "role": "user", "content": "What happened?"}],
                {"sources": {"archive": archive, "ocha": ocha}},
            )
    stream_events(response)
    assert entered == ([True] if archive else [])
    assert len(invocations) == 1
    info = invocations[0]
    tools = {tool.name for tool in info.function_tools}
    assert "mapping" in tools
    assert ("search_archive" in tools) == (archive and not offline)
    assert ("search_ocha_corpus" in tools) == ocha
    for tool_name in (
        "search_bearing_witness_document",
        "read_bearing_witness_pages",
        "search_faqs",
        "read_faq",
        "list_project_metadata_records",
        "read_project_metadata_record",
    ):
        assert (tool_name in tools) == archive
    assert ("Use the Bearing Witness archive tools" in info.instructions) == archive
    assert ("currently the only platform" in info.instructions) == archive
    assert ("only a small selection of reports" in info.instructions) == ocha


@pytest.mark.parametrize(("ocha", "expected_ids"), [(False, [2]), (True, [1, 2])])
def test_archive_search_filters_ocha_items_only_when_ocha_disabled(
    chat, monkeypatch, ocha, expected_ids,
):
    from ochask.web.app import app
    from pydantic_ai.messages import ToolReturnPart

    results = []

    def search_archive(query: str, limit: int = 10) -> dict:
        """Search the test archive."""
        return {"cataloged": {"total": 2, "hits": [
            {
                "item_id": 1,
                "platform": "OCHA (UN office for the Coordination of Humanitarian Affairs)",
                "url": "https://www.ochaopt.org/content/hostilities-gaza-strip-and-israel-flash-update-15",
            },
            {
                "item_id": 2,
                "platform": "Human Rights Watch",
                "url": "https://www.hrw.org/news/2025/08/01/rights-in-focus?ref=ocha",
            },
        ]}, "unindexed": {"total": 1, "hits": [
            {"item_id": 3, "url": "https://www.unocha.org/example", "snippet": "OCHA evidence"},
        ]}}

    fake_archive = chat.OptionalArchiveToolset(FunctionToolset([search_archive]))
    monkeypatch.setattr(chat, "archive_toolset", fake_archive)

    async def respond(messages, _info):
        returns = [
            part.content
            for message in messages for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns:
            results.append(returns[-1])
            yield "Answer"
        else:
            yield {0: DeltaToolCall(name="search_archive", json_args='{"query":"trucks","limit":10}')}

    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            events = stream_events(post_run(
                client,
                [{"id": "question", "role": "user", "content": "How many trucks?"}],
                {"sources": {"archive": True, "ocha": ocha}},
            ))

    assert results
    hits = results[0]["cataloged"]["hits"]
    assert [hit["item_id"] for hit in hits] == expected_ids
    assert bool(results[0]["unindexed"]["hits"]) == ocha
    assert ("total" in results[0]["cataloged"]) == ocha
    sources, = [event["snapshot"]["ochaskSourceContext"]["sources"]
                for event in events if event["type"] == "STATE_SNAPSHOT"]
    assert sources == (["archive", "ocha"] if ocha else ["archive"])


def test_archive_lookup_filters_nested_ocha_record(chat):
    result = chat.tools._filter_ocha_payload({
        "url": "https://reliefweb.int/report/example",
        "in_archive": True,
        "record": {"author": "UN Office for the Coordination of Humanitarian Affairs", "description": "Evidence"},
    })
    assert result == {"error": "This archived item belongs to the disabled OCHA source."}


def test_archive_history_with_ocha_records_is_dropped_when_ocha_is_disabled(chat):
    from ag_ui.core import AssistantMessage, FunctionCall, ToolCall, ToolMessage, UserMessage
    from ochask.web.chat_options import ChatOptions, filter_history

    question = UserMessage(id="q1", content="First question")
    call = AssistantMessage(id="a1", tool_calls=[ToolCall(
        id="call", function=FunctionCall(name="search_archive", arguments="{}"),
    )])
    tool = ToolMessage(id="t1", tool_call_id="call", content=json.dumps({
        "unindexed": {"hits": [{"url": "https://ochaopt.org/example"}]},
    }))
    answer = AssistantMessage(id="a2", content="OCHA answer")
    options = ChatOptions(
        sources={"archive": True, "ocha": False}, priorTurnSources={"q1": {"archive"}},
    )
    retained, sources = filter_history([question, call, tool, answer], options)
    assert retained == [question]
    assert not sources


@pytest.mark.parametrize(
    ("archive", "ocha", "expected"),
    [
        (False, True, "Archive is currently disabled"),
        (True, False, "OCHA report corpus is currently disabled"),
    ],
)
def test_disabled_source_instructions_name_disabled_source(
    chat, monkeypatch, archive, ocha, expected,
):
    from ochask.web.app import app

    invocations = []

    def search_archive(query: str) -> list:
        """Search the test archive."""
        return []

    fake_archive = chat.OptionalArchiveToolset(FunctionToolset([search_archive]))
    monkeypatch.setattr(chat, "archive_toolset", fake_archive)

    async def respond(messages, info):
        invocations.append(info)
        yield "Test answer"

    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            stream_events(post_run(
                client,
                [{"id": "question", "role": "user", "content": "What happened?"}],
                {"sources": {"archive": archive, "ocha": ocha}},
            ))

    assert len(invocations) == 1
    instructions = invocations[0].instructions
    assert expected in instructions
    assert "ask the user to enable it" in instructions


def test_mapping_streams_tool_result_without_research_source_dependency(chat, monkeypatch):
    from ochask import geocoding
    from ochask.web.app import app
    from pydantic_ai.messages import ToolReturnPart

    place = {"id": "osm-1", "label": "Test place", "lat": 51.5, "lng": -0.12}
    monkeypatch.setattr(geocoding, "_lookup", lambda query, **kwargs: ([place], None))

    async def respond(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returns:
            assert returns[-1].content["places"] == [place]
            yield "Here is the map."
        else:
            yield {0: DeltaToolCall(name="mapping", json_args='{"locations":["Test place"]}')}

    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            events = stream_events(post_run(
                client, [{"id": "map-question", "role": "user", "content": "Map Test place"}],
                {"sources": {"archive": False, "ocha": True}},
            ))

    start = next(event for event in events if event["type"] == "TOOL_CALL_START")
    result = next(event for event in events if event["type"] == "TOOL_CALL_RESULT")
    assert start["toolCallName"] == "mapping"
    assert start["toolCallId"] == result["toolCallId"]
    assert json.loads(result["content"])["places"] == [place]
    context = next(event["snapshot"]["ochaskSourceContext"] for event in events if event["type"] == "STATE_SNAPSHOT")
    assert context == {"userMessageId": "map-question", "sources": []}


@pytest.mark.parametrize(
    "settings",
    [
        {"sources": {"archive": False, "ocha": False}},
        {"sources": {"archive": "false", "ocha": True}},
        {"modelId": "unconfigured-model"},
        {"reasoningEffort": "medium"},
        {"reasoningEffort": "xhigh"},
    ],
)
def test_invalid_settings_rejected_before_agent(chat, monkeypatch, settings):
    from ochask.web.app import app
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    async def must_not_dispatch(*args, **kwargs):
        raise AssertionError("Rejected request attempted to start AG-UI")

    monkeypatch.setattr(AGUIAdapter, "from_request", must_not_dispatch)
    with TestClient(app, base_url="http://localhost") as client:
        response = post_run(client, [{"id": "q", "role": "user", "content": "Question"}], settings)
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("configured", "effort", "expected"),
    [
        ("openai:gpt-4.1-mini", None, None),
        ("openai:gpt-6-luna", None, "low"),
        ("openai:gpt-6-luna", "high", "high"),
        ("openai-chat:gpt-6-luna", "high", "high"),
        ("gpt-6-luna", "low", "low"),
    ],
)
def test_config_and_model_settings(chat, monkeypatch, configured, effort, expected):
    from ochask.web.app import app
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    monkeypatch.setenv("OPENAI_CHAT_MODEL", configured)
    calls = []

    def capture_run(adapter, **kwargs):
        calls.append(kwargs)
        return iter(())

    def empty_response(adapter, _stream):
        from starlette.responses import Response
        return Response()

    monkeypatch.setattr(AGUIAdapter, "run_stream", capture_run)
    monkeypatch.setattr(AGUIAdapter, "streaming_response", empty_response)
    settings = {"modelId": "gpt-6-luna", "reasoningEffort": effort} if effort else None
    with TestClient(app, base_url="http://localhost") as client:
        config = client.get("/api/chat/config").json()["model"]
        assert config["id"] == configured.rsplit(":", 1)[-1]
        assert config["defaultReasoningEffort"] == ("low" if expected else None)
        response = post_run(client, [{"id": "q", "role": "user", "content": "Question"}], settings)

    assert response.status_code == 200
    run = calls[0]
    assert run["deps"].enabled_sources == {"archive", "ocha"}
    if expected:
        assert run["model"] == "openai-responses:gpt-6-luna"
        assert run["model_settings"] == {
            "openai_reasoning_effort": expected,
            "openai_store": False,
            "max_tokens": 25_000,
        }
        assert run["usage_limits"].output_tokens_limit == 25_000
    else:
        assert run["model"] == configured
        assert run["model_settings"] is None
        assert run["usage_limits"].output_tokens_limit == 2_048


def test_disabled_source_history_and_new_single_source_turn(chat, monkeypatch):
    from ochask.web.app import app
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    inspected_history = []
    archives = [
        {"id": "q1", "role": "user", "content": "First question"},
        {
            "id": "archive-call", "role": "assistant", "content": "",
            "toolCalls": [{
                "id": "archive-result", "type": "function",
                "function": {"name": "search_archive", "arguments": "{}"},
            }],
        },
        {"id": "result1", "role": "tool", "toolCallId": "archive-result", "content": "Archive evidence"},
        {"id": "answer1", "role": "assistant", "content": "Archive answer"},
        {"id": "q2", "role": "user", "content": "Earlier follow-up"},
        {"id": "answer2", "role": "assistant", "content": "Derived from previous answer"},
    ]
    monkeypatch.setattr(chat, "search", lambda *args, **kwargs: [])

    async def respond(messages, info):
        inspected_history.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        if len(inspected_history) == 1:
            yield {0: DeltaToolCall(name="search_ocha_corpus", json_args='{"query":"water"}')}
        else:
            yield "Only OCHA"

    settings = {"sources": {"archive": False, "ocha": True}}
    q3 = {"id": "q3", "role": "user", "content": "Question scoped to OCHA"}
    with chat.agent.override(model=StreamingModel(stream_function=respond)):
        with TestClient(app, base_url="http://localhost") as client:
            events = stream_events(post_run(client, [*archives, q3], settings))
            context, = [
                event["snapshot"]["ochaskSourceContext"]
                for event in events if event["type"] == "STATE_SNAPSHOT"
            ]
            assert context == {"userMessageId": "q3", "sources": ["ocha"]}
            response = post_run(client, [
                *archives, q3,
                {"id": "answer3", "role": "assistant", "content": "Only OCHA"},
                {"id": "q4", "role": "user", "content": "Next follow-up"},
            ], {**settings, "priorTurnSources": {"q3": context["sources"]}})
            stream_events(response)

    assert len(inspected_history) == 3
    for message_history in inspected_history:
        for excluded in ("search_archive", "Archive evidence", "Archive answer", "Derived from previous answer"):
            assert excluded not in message_history
        assert "First question" in message_history
    assert "Only OCHA" in inspected_history[-1]
