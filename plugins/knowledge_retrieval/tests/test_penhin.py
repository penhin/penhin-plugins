import asyncio

import pytest

penhin = pytest.importorskip("penhin", reason="Install the supported Penhin checkout")
pytestmark = pytest.mark.integration

from penhin import (  # noqa: E402
    Agent,
    CancellationToken,
    ModelClient,
    PluginRuntime,
    Session,
    ToolContext,
    discover_plugins,
    load_plugin,
)
from penhin.providers import LLMResponse, LLMUsage  # noqa: E402

from penhin_knowledge import AccessScope, create_plugin  # noqa: E402


class Provider:
    retry_errors = ()

    def __init__(self):
        self.requests = []

    def create_message(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                [
                    {
                        "type": "tool_use",
                        "id": "search-1",
                        "name": "knowledge_retrieval__search",
                        "input": {"question": "数据库备份什么时候执行？"},
                    }
                ],
                "tool_use",
                LLMUsage(),
            )
        return LLMResponse([{"type": "text", "text": "done"}], "end_turn", LLMUsage())

    def stream_message(self, request, callback):
        return self.create_message(request)


@pytest.mark.parametrize("reference", ["knowledge_retrieval", "penhin_knowledge:create_plugin"])
async def test_real_entrypoint_agent_search_citations_and_lifecycle(
    service, source, scope, reference
):
    await service.upsert("project", [source])
    calls = []

    async def resolve(context):
        calls.append((context.session_id, context.invocation_id))
        return scope

    assert "knowledge_retrieval" in discover_plugins()
    plugin = load_plugin(reference, service=service, resolve_scope=resolve)
    provider, session = Provider(), Session()
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime, system="trusted system")
        assert (await agent.run(session, "备份时间？")).status == "complete"
    assert service.closed
    assert calls == [(session.id, "search-1")]
    assert len(provider.requests) == 2
    messages = str(provider.requests[1].messages)
    assert "每天凌晨" in messages and "citation_id" in messages and "untrusted" in messages
    assert all(request.system == "trusted system" for request in provider.requests)


async def test_fetch_reauthorizes_and_does_not_trust_model_scope(service, source, scope):
    await service.upsert("project", [source])
    allowed = True
    calls = 0

    async def resolve(context):
        nonlocal calls
        calls += 1
        assert context.session_id == "session"
        return scope if allowed else AccessScope("project", ())

    plugin = create_plugin(service=service, resolve_scope=resolve)
    search, fetch = plugin.tools
    context = ToolContext("session", "invocation", CancellationToken())
    assert set(search.parameters.model_fields) == {"question", "limit"}
    result = await search.invoke({"question": "备份"}, context)
    citation_id = result.value["hits"][0]["citation"]["citation_id"]
    original = await fetch.invoke({"citation_id": citation_id}, context)
    assert original.value["text"] == source.parts[0].text
    allowed = False
    assert (
        await fetch.invoke({"citation_id": citation_id}, context)
    ).error == "citation_unavailable"
    assert calls == 3


async def test_denied_scope_errors_redacted_and_cancelled_before_authorization(service):
    async def resolve(context):
        raise PermissionError("secret")

    plugin = create_plugin(service=service, resolve_scope=resolve)
    context = ToolContext("session", "invocation", CancellationToken())
    assert (await plugin.tools[0].invoke({"question": "query"}, context)).error == "access_denied"
    context.cancellation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await plugin.tools[0].invoke({"question": "query"}, context)


@pytest.mark.parametrize("shared", [True, False])
async def test_default_has_no_tools_and_respects_service_ownership(service, shared):
    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service, close_service=not shared))
        assert runtime.catalog() == {}
    assert service.closed is not shared


async def test_deactivation_cancels_pending_authorization(service):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def resolve(context):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    plugin = create_plugin(service=service, resolve_scope=resolve)
    context = ToolContext("session", "invocation", CancellationToken())
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        task = asyncio.create_task(plugin.tools[0].invoke({"question": "query"}, context))
        await entered.wait()
        await runtime.deactivate("knowledge_retrieval")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()


async def test_existing_asr_and_media_contracts_feed_index(service):
    asr = pytest.importorskip("penhin_asr", reason="Optional cross-plugin integration")
    media = pytest.importorskip("penhin_media", reason="Optional cross-plugin integration")
    from penhin_knowledge import KnowledgeSource

    transcript = asr.Transcript("周五发布。", "voice", 4.0)
    supplement = media.ContextSupplement(
        (
            media.SupplementPart(
                "pdf",
                "application/pdf",
                0,
                0,
                "pdf_page",
                "发布计划图表。",
                "发布日期为周五。",
                page=1,
            ),
        ),
        "test-vision",
    )
    await service.upsert(
        "p",
        [
            KnowledgeSource.from_transcript(transcript, title="会议"),
            KnowledgeSource.from_media(supplement, source_id="pdf", title="计划书"),
        ],
    )
    hits = (await service.search("发布", scope=AccessScope("p", ("voice", "pdf")))).hits
    assert {hit.citation.content_type for hit in hits} == {
        "transcript",
        "extracted_text",
        "generated_description",
    }
