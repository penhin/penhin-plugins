import asyncio
from types import SimpleNamespace

import pytest

from penhin_tts import SpeechPlayer, TtsService, VoiceInteraction


async def test_play_segments_in_order(backend, sink):
    async with SpeechPlayer(TtsService(backend), sink) as player:
        await player.speak("第一句。第二句。", response_id="r1")
    assert [s.index for s in sink.writes] == [0, 1]
    assert sink.stops and player.closed and player.service.closed


@pytest.mark.parametrize("method", ["interrupt", "task", "token", "close"])
async def test_interrupt_stops_device_before_slow_inference_cleanup(backend, sink, method):
    sink.gate = asyncio.Event()
    backend.cleanup_gate = asyncio.Event()
    signal = SimpleNamespace(cancelled=False)
    player = SpeechPlayer(TtsService(backend), sink)
    task = asyncio.create_task(player.speak("第一句。第二句。第三句。", cancellation=signal))
    await sink.entered.wait()
    if method == "interrupt":
        stopping = asyncio.create_task(player.interrupt())
    elif method == "close":
        stopping = asyncio.create_task(player.aclose())
    else:
        stopping = None
        if method == "task":
            task.cancel()
        else:
            signal.cancelled = True
    for _ in range(100):
        if sink.stops:
            break
        await asyncio.sleep(0.005)
    assert sink.stops > 0 and len(sink.writes) == 1
    backend.cleanup_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    if stopping:
        await asyncio.wait_for(stopping, 1)
    if method != "close":
        sink.gate = None
        await player.speak("新回答", response_id="r2")
        assert [s.response_id for s in sink.writes] == ["speech", "r2"]


async def test_voice_turn_uses_final_asr_and_closes_answer_on_barge_in(backend, sink):
    class Asr:
        async def transcribe(self, audio, **kwargs):
            assert audio == "host-audio"
            return SimpleNamespace(text="用户问题", is_final=True)

    closed = asyncio.Event()
    prompts = []

    async def respond(text):
        prompts.append(text)
        try:
            yield "回答的第一句。"
            await asyncio.Event().wait()
        finally:
            closed.set()

    sink.gate = asyncio.Event()
    interaction = VoiceInteraction(Asr(), SpeechPlayer(TtsService(backend), sink), respond)
    task = asyncio.create_task(interaction.run_turn("host-audio"))
    await sink.entered.wait()
    await asyncio.wait_for(interaction.interrupt(), 1)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert prompts == ["用户问题"] and closed.is_set() and sink.stops
    await interaction.aclose()


async def test_silence_does_not_invoke_answer(backend, sink):
    class Asr:
        async def transcribe(self, audio, **kwargs):
            return SimpleNamespace(text=" ")

    def never(text):
        raise AssertionError("must not generate an answer for silence")

    interaction = VoiceInteraction(Asr(), SpeechPlayer(TtsService(backend), sink), never)
    await interaction.run_turn(None)
    assert not sink.writes


async def test_concurrent_interrupts_share_cleanup_before_next_response(backend, sink):
    sink.gate = asyncio.Event()
    stopped = asyncio.Event()
    release = asyncio.Event()

    async def slow_stop():
        sink.stops += 1
        stopped.set()
        await release.wait()

    sink.stop = slow_stop
    player = SpeechPlayer(TtsService(backend), sink)
    speaking = asyncio.create_task(player.speak("旧回答。"))
    await sink.entered.wait()
    a = asyncio.create_task(player.interrupt())
    b = asyncio.create_task(player.interrupt())
    await stopped.wait()
    await asyncio.sleep(0)
    assert sink.stops == 1 and not a.done() and not b.done()
    release.set()
    await asyncio.gather(a, b)
    with pytest.raises(asyncio.CancelledError):
        await speaking
    sink.gate = None
    await player.speak("新回答。", response_id="new")
    assert sink.stops == 2 and sink.writes[-1].response_id == "new"


async def test_interrupt_during_asr_closes_recognition_and_can_start_new_turn(backend, sink):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    class Asr:
        block = True

        async def transcribe(self, audio, **kwargs):
            if self.block:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            return SimpleNamespace(text="新问题", is_final=True)

    async def respond(text):
        yield text + "的回答。"

    asr = Asr()
    voice = VoiceInteraction(asr, SpeechPlayer(TtsService(backend), sink), respond)
    task = asyncio.create_task(voice.run_turn(None))
    await entered.wait()
    await voice.interrupt()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and not sink.writes
    asr.block = False
    await voice.run_turn(None, response_id="new")
    assert sink.writes[0].response_id == "new"
    await voice.aclose()
