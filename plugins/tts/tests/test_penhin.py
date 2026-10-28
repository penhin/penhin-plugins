import asyncio

import pytest

penhin = pytest.importorskip("penhin", reason="Install the matching Penhin core")
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

from penhin_tts import SpeechPlayer, TtsService, create_plugin  # noqa: E402


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
                        "id": "call-1",
                        "name": "tts__synthesize",
                        "input": {"text": "你好，中文语音。"},
                    }
                ],
                "tool_use",
                LLMUsage(),
            )
        return LLMResponse([{"type": "text", "text": "done"}], "end_turn", LLMUsage())

    def stream_message(self, request, callback):
        result = self.create_message(request)
        for block in result.content:
            if block["type"] == "text":
                callback(block["text"])
        return result


@pytest.mark.parametrize("reference", ["tts", "penhin_tts:create_plugin"])
async def test_entrypoint_real_agent_and_host_attachment(backend, reference):
    service = TtsService(backend)
    calls = []

    async def publish(audio, context):
        assert audio.to_wav().startswith(b"RIFF")
        calls.append((context.session_id, context.invocation_id))
        return "speech-attachment"

    assert "tts" in discover_plugins()
    plugin = load_plugin(reference, service=service, publish_audio=publish)
    provider, session = Provider(), Session()
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        agent = Agent(ModelClient(provider, "offline"), plugins=runtime)
        assert (await agent.run(session, "生成语音附件")).status == "complete"
        assert calls == [(session.id, "call-1")]
        assert "speech-attachment" in str(provider.requests[1].messages)
    assert service.closed


async def test_lifecycle_only_shutdown_stops_playback(backend, sink):
    sink.gate = asyncio.Event()
    service = TtsService(backend)
    player = SpeechPlayer(service, sink)
    plugin = create_plugin(service=service, player=player)
    assert not plugin.tools
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        task = asyncio.create_task(player.speak("你好。"))
        await sink.entered.wait()
        await runtime.deactivate("tts")
        with pytest.raises(asyncio.CancelledError):
            await task
    assert service.closed and sink.stops


async def test_publisher_error_redacted_and_host_ownership(backend):
    async def denied(*args):
        raise PermissionError("secret path")

    service = TtsService(backend)
    plugin = create_plugin(service=service, publish_audio=denied, close_service=False)
    context = ToolContext("session", "invocation", CancellationToken())
    async with PluginRuntime() as runtime:
        await runtime.activate(plugin)
        result = await plugin.tools[0].invoke({"text": "你好"}, context)
        assert result.error == "audio_publish_failed"
    assert not service.closed


async def test_token_cancels_publication(backend):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    plugin = create_plugin(service=TtsService(backend), publish_audio=blocked)
    token = CancellationToken()
    task = asyncio.create_task(
        plugin.tools[0].invoke({"text": "你好"}, ToolContext("session", "invocation", token))
    )
    await entered.wait()
    token.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert cleaned.is_set()


async def test_real_agent_delta_bridge_and_voice_controller(backend, sink):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from penhin_tts import VoiceInteraction

    path = Path(__file__).parents[1] / "examples" / "penhin_voice.py"
    spec = importlib.util.spec_from_file_location("penhin_voice_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Asr:
        async def transcribe(self, audio, **kwargs):
            return SimpleNamespace(text="用户提交的问题", is_final=True)

    class AnswerProvider:
        retry_errors = ()

        def stream_message(self, request, callback):
            assert "用户提交的问题" in str(request.messages)
            callback("你好。")
            callback("这是流式回答。")
            return LLMResponse(
                [{"type": "text", "text": "你好。这是流式回答。"}], "end_turn", LLMUsage()
            )

    player = SpeechPlayer(TtsService(backend), sink)
    voice = VoiceInteraction(
        Asr(), player, module.make_responder(ModelClient(AnswerProvider(), "offline"), Session())
    )
    try:
        await asyncio.wait_for(voice.run_turn(None), 2)
        assert [s.text for s in sink.writes] == ["你好。", "这是流式回答。"]
    finally:
        await voice.aclose()
