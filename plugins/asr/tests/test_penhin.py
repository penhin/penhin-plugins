import asyncio

import pytest

penhin = pytest.importorskip("penhin", reason="Install the pinned Penhin checkout for integration")
pytestmark = pytest.mark.integration

from penhin import (  # noqa: E402
    Agent,
    CancellationToken,
    ModelClient,
    Plugin,
    PluginRuntime,
    Session,
    ToolContext,
    discover_plugins,
    load_plugin,
)
from penhin.providers import LLMResponse, LLMUsage  # noqa: E402
from test_service import Blocking, Immediate  # noqa: E402

from penhin_asr import AsrError, AsrService, AudioInput, create_plugin  # noqa: E402


class Provider:
    retry_errors = ()

    def __init__(self):
        self.requests = []

    def create_message(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            content = [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "asr__transcribe",
                    "input": {"attachment_id": "attachment-1"},
                }
            ]
        else:
            content = [{"type": "text", "text": "done"}]
        return LLMResponse(
            content, "tool_use" if len(self.requests) == 1 else "end_turn", LLMUsage()
        )

    def stream_message(self, request, callback):
        result = self.create_message(request)
        for block in result.content:
            if block["type"] == "text":
                callback(block["text"])
        return result


@pytest.mark.parametrize("reference", ["asr", "penhin_asr:create_plugin"])
async def test_entry_point_and_real_agent_tool_path(audio, reference):
    calls = []

    async def read_audio(attachment_id, context):
        calls.append((attachment_id, context.session_id, context.invocation_id))
        return audio

    service = AsrService(Immediate())
    assert "asr" in discover_plugins()
    plugin = load_plugin(reference, service=service, read_audio=read_audio)
    provider = Provider()
    session = Session()
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime)
        assert (await agent.run(session, "Transcribe the attachment")).status == "complete"
        assert len(provider.requests) == 2
        assert calls == [("attachment-1", session.id, "call-1")]
        assert "你好，世界。" in str(provider.requests[1].messages)
    assert service.closed


async def test_direct_transcription_does_not_call_model_until_host_submits(audio):
    provider = Provider()
    service = AsrService(Immediate())

    async def read_audio(attachment_id, context):
        return audio

    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service, read_audio=read_audio))
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime)
        editable_text = (await service.transcribe(audio)).text
        assert editable_text == "你好，世界。" and provider.requests == []
        await agent.run(Session(), editable_text + "（用户修改后提交）")
        assert provider.requests


async def test_attachment_reader_errors_and_mismatch_are_safe(audio):
    service = AsrService(Immediate())

    async def denied(attachment_id, context):
        raise PermissionError("secret account path")

    async def wrong(attachment_id, context):
        return AudioInput(audio.data, "wrong-attachment")

    context = ToolContext("session", "invocation", CancellationToken())
    for reader, error in [(denied, "attachment_unavailable"), (wrong, "attachment_mismatch")]:
        plugin = create_plugin(service=service, read_audio=reader)
        result = await plugin.tools[0].invoke({"attachment_id": audio.source_id}, context)
        assert result.error == error
    await service.aclose()


async def test_deactivation_cancels_direct_transcription_without_harming_other_plugins(audio):
    backend = Blocking()
    service = AsrService(backend)

    async def reader(attachment_id, context):
        return audio

    async with PluginRuntime() as runtime:
        await runtime.activate(create_plugin(service=service, read_audio=reader))
        await runtime.activate(Plugin("other", "1"))
        task = asyncio.create_task(service.transcribe(audio))
        await backend.entered.wait()
        stopping = asyncio.create_task(runtime.deactivate("asr"))
        await backend.cleaning.wait()
        assert not stopping.done()
        backend.release.set()
        await stopping
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [plugin.name for plugin in runtime.active] == ["other"]


async def test_shared_service_has_explicit_host_ownership(audio):
    service = AsrService(Immediate())

    async def reader(attachment_id, context):
        return audio

    async with PluginRuntime() as runtime:
        await runtime.activate(
            create_plugin(service=service, read_audio=reader, close_service=False)
        )
    assert not service.closed
    assert (await service.transcribe(audio)).text
    await service.aclose()


class VoiceProvider(Provider):
    def create_message(self, request):
        self.requests.append(request)
        return LLMResponse(
            [{"type": "text", "text": "模型回复，可交给后续 TTS"}], "end_turn", LLMUsage()
        )


@pytest.mark.parametrize("reference", ["asr", "penhin_asr:create_plugin"])
async def test_voice_delivery_uses_user_text_without_exposing_asr_tool(audio, reference):
    backend, provider, session = Immediate(), VoiceProvider(), Session()
    service = AsrService(backend)
    reads = []

    async def reader(source_id):
        reads.append(source_id)
        return audio

    async with PluginRuntime() as runtime:
        await runtime.activate(load_plugin(reference, service=service))
        assert runtime.catalog() == {}
        voice = await service.prepare_voice(audio.source_id, read_audio=reader)
        assert not provider.requests
        display = voice.as_display_message()
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime, system="trusted system")
        assert (await agent.run(session, voice.model_text)).status == "complete"
        assert provider.requests[0].system == "trusted system"
        assert provider.requests[0].messages[0] == {"role": "user", "content": voice.model_text}
        assert "text" not in display and "model_text" not in display
        # The internal text remains available on later turns without recognizing again.
        await agent.run(session, "继续")
        assert voice.model_text in str(provider.requests[1].messages)
        assert reads == [audio.source_id] and len(backend.calls) == 1
    assert service.closed


@pytest.mark.parametrize("failure", ["no_speech", "denied", "cancelled"])
async def test_failed_voice_preparation_never_calls_model_or_appends_query(audio, failure):
    provider, session = VoiceProvider(), Session()
    token = CancellationToken()
    if failure == "cancelled":
        token.cancel()

    async def reader(_):
        if failure == "denied":
            raise PermissionError("private attachment")
        return audio

    async with AsrService(Immediate("")) as service:
        with pytest.raises(asyncio.CancelledError if failure == "cancelled" else AsrError):
            voice = await service.prepare_voice(
                audio.source_id, read_audio=reader, cancellation=token
            )
            await Agent(ModelClient(provider, "offline")).run(
                session, voice.model_text, cancellation=token
            )
    assert not provider.requests and not session.state.messages
