import asyncio
import io
import wave
from types import SimpleNamespace

import pytest

from penhin_tts import (
    BackendError,
    InvalidText,
    SpeechAudio,
    TtsBusy,
    TtsClosed,
    TtsLimits,
    TtsService,
    TtsTimeout,
)


async def collect(service, source, **kwargs):
    async with service.stream(source, **kwargs) as stream:
        return [part async for part in stream]


async def test_complete_wav_and_segment_metadata(backend):
    async with TtsService(backend) as service:
        audio = await service.synthesize("你好。欢迎使用中文语音！", speed=1.2)
        with wave.open(io.BytesIO(audio.to_wav())) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            assert wav.getnframes() == 1600
        parts = await collect(service, "你好。再见。", response_id="answer-1")
        assert [x.index for x in parts] == [0, 1]
        assert all(x.response_id == "answer-1" for x in parts)
        assert backend.sessions == 2
        assert backend.texts[:2] == [("你好。", 1.2), ("欢迎使用中文语音！", 1.2)]
    assert service.closed and not backend.active


async def test_stream_emits_before_input_eof_and_preserves_tail(backend):
    gate = asyncio.Event()

    async def deltas():
        yield "你好"
        yield "。第二"
        await gate.wait()
        yield "句"

    async with TtsService(backend) as service:
        async with service.stream(deltas()) as output:
            first = await asyncio.wait_for(anext(output), 1)
            assert first.text == "你好。"
            gate.set()
            rest = [part async for part in output]
            assert [p.text for p in rest] == ["第二句"]
    assert backend.sessions == 1


async def test_latency_flush_does_not_cancel_pending_text_read(backend):
    gate = asyncio.Event()
    cancelled = False

    async def deltas():
        nonlocal cancelled
        try:
            yield "还没有标点"
            await gate.wait()
            yield "后半句"
        except asyncio.CancelledError:
            cancelled = True
            raise

    service = TtsService(backend, limits=TtsLimits(flush_seconds=0.02))
    async with service.stream(deltas()) as out:
        assert (await asyncio.wait_for(anext(out), 1)).text == "还没有标点"
        assert not cancelled
        gate.set()
        assert [x.text async for x in out] == ["后半句"]


async def test_no_punctuation_is_bounded_and_decimal_preserved(backend):
    service = TtsService(backend, limits=TtsLimits(max_segment_chars=12))
    parts = await collect(service, "价格3.14元。" + "中" * 27)
    assert [x.text for x in parts] == ["价格3.14元。", "中" * 12, "中" * 12, "中" * 3]


async def test_empty_text_is_valid_silence_without_model(backend):
    assert (await TtsService(backend).synthesize(" ？！\n ")).pcm == b""
    assert backend.sessions == 0


@pytest.mark.parametrize("value", [None, b"hello", 12])
async def test_bad_input(backend, value):
    with pytest.raises(InvalidText):
        await collect(TtsService(backend), value)


@pytest.mark.parametrize("speed", [0, 3, float("nan"), float("inf"), True, "fast"])
async def test_invalid_speed(backend, speed):
    with pytest.raises(InvalidText, match="invalid_speed"):
        await collect(TtsService(backend), "你好", speed=speed)


async def test_cumulative_input_and_output_limits(backend):
    service = TtsService(backend, limits=TtsLimits(max_text_chars=4, max_segment_chars=4))
    with pytest.raises(InvalidText, match="text_too_large"):
        await service.synthesize("中" * 5)
    assert backend.sessions == 0

    async def chunks():
        yield "你好。"
        yield "再见。"

    with pytest.raises(InvalidText, match="text_too_large"):
        await collect(service, chunks())
    service = TtsService(backend, limits=TtsLimits(max_audio_seconds=0.06))
    with pytest.raises(BackendError, match="audio_too_long"):
        await service.synthesize("你好。再见。")


@pytest.mark.parametrize(
    "audio", [SpeechAudio(b"x", 16000), SpeechAudio(b"", 16000), SpeechAudio(b"00", 0), "wrong"]
)
async def test_invalid_backend_output(backend, audio):
    backend.result = audio
    with pytest.raises(BackendError, match="invalid_backend_audio"):
        await TtsService(backend).synthesize("你好")


async def test_consumer_backpressure_bounds_ingestion(backend):
    reads = 0

    async def deltas():
        nonlocal reads
        for _ in range(1000):
            reads += 1
            yield "一段。"

    async with TtsService(backend).stream(deltas()) as out:
        await anext(out)
        await asyncio.sleep(0.05)
        assert reads <= 3
    assert not backend.active


@pytest.mark.parametrize("method", ["task", "token", "close", "interrupt"])
async def test_cancel_and_reuse(backend, method):
    backend.gate = asyncio.Event()
    signal = SimpleNamespace(cancelled=False)
    service = TtsService(backend)
    task = asyncio.create_task(service.synthesize("你好", cancellation=signal))
    await backend.entered.wait()
    with pytest.raises(TtsBusy):
        await service.synthesize("忙")
    if method == "task":
        task.cancel()
    elif method == "token":
        signal.cancelled = True
    elif method == "close":
        await service.aclose()
    else:
        await service.interrupt()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert not backend.active
    backend.gate = None
    if method == "close":
        with pytest.raises(TtsClosed):
            await service.synthesize("你好")
    else:
        assert (await service.synthesize("新回答")).pcm


async def test_repeated_cancellation_waits_for_cleanup(backend):
    backend.gate = asyncio.Event()
    backend.cleanup_gate = asyncio.Event()
    service = TtsService(backend)
    task = asyncio.create_task(service.synthesize("你好"))
    await backend.entered.wait()
    task.cancel()
    await backend.cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(TtsBusy):
        await service.synthesize("你好")
    backend.cleanup_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not backend.active


async def test_inference_and_idle_input_timeout(backend):
    backend.gate = asyncio.Event()
    service = TtsService(backend, timeout=0.02)
    with pytest.raises(TtsTimeout):
        await service.synthesize("你好")
    assert not backend.active

    async def never():
        await asyncio.Event().wait()
        yield "never"

    with pytest.raises(TtsTimeout):
        await collect(service, never())


async def test_error_does_not_disclose_input_or_paths(backend):
    async def fail(*args, **kwargs):
        raise RuntimeError("secret text C:/private")

    backend.generate = fail
    with pytest.raises(BackendError) as error:
        await TtsService(backend).synthesize("secret text")
    assert str(error.value) == "synthesis_failed"


async def test_interrupt_discards_already_completed_queued_audio(backend):
    service = TtsService(backend)
    async with service.stream("你好。") as out:
        await backend.cleaning.wait()
        await service.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await anext(out)


async def test_input_failure_after_partial_output_is_not_success(backend):
    gate = asyncio.Event()

    async def source():
        yield "第一句。"
        await gate.wait()
        raise RuntimeError("private upstream response")

    async with TtsService(backend).stream(source()) as out:
        assert (await anext(out)).text == "第一句。"
        gate.set()
        with pytest.raises(BackendError, match="synthesis_failed"):
            await anext(out)
