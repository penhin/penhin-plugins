import asyncio
import copy

import pytest

penhin = pytest.importorskip("penhin", reason="Install the supported Penhin checkout")
pytestmark = pytest.mark.integration

from penhin import (  # noqa: E402
    Agent,
    CancellationToken,
    Context,
    ModelClient,
    Plugin,
    PluginRuntime,
    Session,
    ToolContext,
    discover_plugins,
    load_plugin,
)
from penhin.providers import LLMResponse, LLMUsage  # noqa: E402
from test_service import Backend, Blocking, Renderer  # noqa: E402

from penhin_media import MediaError, MediaService, create_plugin  # noqa: E402


class Provider:
    retry_errors = ()

    def __init__(self, tool=False):
        self.requests = []
        self.tool = tool

    def create_message(self, request):
        self.requests.append(request)
        if self.tool and len(self.requests) == 1:
            return LLMResponse(
                [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "media_understanding__understand",
                        "input": {"attachment_ids": ["image-1"]},
                    }
                ],
                "tool_use",
                LLMUsage(),
            )
        return LLMResponse([{"type": "text", "text": "done"}], "end_turn", LLMUsage())

    def stream_message(self, request, callback):
        return self.create_message(request)


@pytest.mark.parametrize("reference", ["media_understanding", "penhin_media:create_plugin"])
async def test_real_agent_entrypoint_tool_and_shutdown(media, reference):
    calls = []

    async def reader(source_id, context):
        calls.append((source_id, context.session_id, context.invocation_id))
        return media

    service = MediaService(Backend(), renderer=Renderer())
    assert "media_understanding" in discover_plugins()
    plugin = load_plugin(reference, service=service, read_attachment=reader)
    provider, session = Provider(tool=True), Session()
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime, system="system remains")
        assert (await agent.run(session, "Understand image")).status == "complete"
    assert service.closed
    assert calls == [(media.source_id, session.id, "call-1")]
    assert len(provider.requests) == 2
    assert "untrusted description" in str(provider.requests[1].messages)
    assert all(r.system == "system remains" for r in provider.requests)


async def test_fallback_prepares_before_provider_and_projects_only_into_user_message(media):
    provider = Provider()
    calls = []

    async def reader(_):
        calls.append("read")
        return media

    async def enable():
        calls.append("enable")
        return True

    service = MediaService(Backend(), renderer=Renderer())
    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service))
        supplement = await service.prepare_fallback(
            [media.source_id],
            read_attachment=reader,
            ensure_enabled=enable,
        )
        assert provider.requests == [] and runtime.catalog() == {}

        # Host owns this single-submission projection and the originating message.
        def project(context):
            messages = copy.deepcopy(context.messages)
            message = next(m for m in reversed(messages) if m["role"] == "user")
            message["content"] = [
                {"type": "text", "text": message["content"]},
                supplement.as_text_block(),
            ]
            return Context(context.system, messages)

        agent = Agent(
            ModelClient(provider, "offline"),
            plugins=runtime,
            system="trusted system",
            transform_context=project,
        )
        session = Session()
        assert (await agent.run(session, "Understand my attachment")).status == "complete"
        assert calls == ["enable", "read"]
        assert provider.requests[0].system == "trusted system"
        assert provider.requests[0].messages[0]["role"] == "user"
        assert '"trust": "untrusted"' in provider.requests[0].messages[0]["content"][1]["text"]
        assert "untrusted description" not in str(session.state.messages)


async def test_failed_fallback_blocks_provider(media):
    provider = Provider()

    async def reader(_):
        raise OSError("private attachment path")

    async def enable():
        return True

    async with MediaService(Backend(), renderer=Renderer()) as service:
        with pytest.raises(MediaError, match="^attachment_unavailable$"):
            await service.prepare_fallback(
                [media.source_id], read_attachment=reader, ensure_enabled=enable
            )
            await Agent(ModelClient(provider, "offline")).run(Session(), "query")
    assert provider.requests == []


async def test_tool_read_is_cancellable_and_failure_is_redacted(media):
    async def denied(source_id, context):
        raise PermissionError("secret")

    async with MediaService(Backend(), renderer=Renderer()) as service:
        plugin = create_plugin(service=service, read_attachment=denied)
        context = ToolContext("session", "invocation", CancellationToken())
        result = await plugin.tools[0].invoke({"attachment_ids": [media.source_id]}, context)
        assert result.error == "attachment_unavailable"
        context.cancellation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await plugin.tools[0].invoke({"attachment_ids": [media.source_id]}, context)


async def test_deactivation_cleans_up_without_affecting_other_plugins(media):
    backend = Blocking()
    service = MediaService(backend, renderer=Renderer())

    async def reader(_):
        return media

    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service))
        await runtime.activate(Plugin("other", "1"))
        task = asyncio.create_task(service.understand([media.source_id], read_attachment=reader))
        await backend.entered.wait()
        closing = asyncio.create_task(runtime.deactivate("media_understanding"))
        await backend.cleaning.wait()
        assert not closing.done()
        backend.release.set()
        await closing
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [p.name for p in runtime.active] == ["other"]


async def test_shared_service_cleanup_is_host_owned(media):
    service = MediaService(Backend(), renderer=Renderer())
    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service, close_service=False))
    assert not service.closed
    await service.aclose()
