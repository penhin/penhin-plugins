import asyncio

import pytest

from penhin_asr import (
    AsrBusy,
    AsrClosed,
    AsrService,
    AsrTimeout,
    AudioLimits,
    BackendError,
    InvalidAudio,
)


class Streaming:
    def __init__(self):
        self.cleaned = False
        self.read = 0

    async def stream(self, chunks, *, sample_rate):
        try:
            async for chunk in chunks:
                self.read += 1
                yield str(self.read), False
            yield "complete", True
        finally:
            self.cleaned = True


async def chunks(*values):
    for value in values:
        yield value


async def test_partial_arrives_before_input_eof_and_final_after_cleanup():
    backend = Streaming()
    first_seen = asyncio.Event()

    async def live_audio():
        yield b"\0\0" * 1600
        await first_seen.wait()
        yield b"\0\0" * 1600

    async with AsrService(backend) as service:
        async with service.stream(live_audio(), sample_rate=16000, source_id="mic") as results:
            partial = await asyncio.wait_for(anext(results), 1)
            assert partial.text == "1" and not partial.is_final
            assert partial.duration_seconds == 0.1
            first_seen.set()
            remaining = [result async for result in results]
            assert [result.is_final for result in remaining] == [False, True]
            assert remaining[-1].as_dict() == {
                "text": "complete",
                "source_id": "mic",
                "duration_seconds": 0.2,
                "is_final": True,
            }
            assert backend.cleaned


async def test_backpressure_early_exit_and_exclusive_service(audio):
    backend = Streaming()
    async with AsrService(backend) as service:
        async with service.stream(chunks(*([b"\0\0"] * 100)), sample_rate=16000, source_id="mic"):
            await asyncio.sleep(0.05)
            assert backend.read <= 2
            with pytest.raises(AsrBusy):
                await service.transcribe(audio)
            with pytest.raises(AsrBusy):
                async with service.stream(chunks(b"\0\0"), sample_rate=16000, source_id="mic"):
                    pass
        assert backend.cleaned
        async with service.stream(chunks(b"\0\0"), sample_rate=16000, source_id="next") as results:
            assert (await anext(results)).source_id == "next"


@pytest.mark.parametrize(
    "values,limits,code",
    [
        ([], AudioLimits(), "empty_audio"),
        ([b""], AudioLimits(), "expected_pcm16_chunk"),
        ([b"x"], AudioLimits(), "expected_pcm16_chunk"),
        (["secret"], AudioLimits(), "expected_pcm16_chunk"),
        ([b"\0" * 32002], AudioLimits(), "chunk_too_large"),
        ([b"\0" * 100, b"\0" * 100], AudioLimits(max_bytes=150), "audio_too_large"),
        ([b"\0" * 100, b"\0" * 100], AudioLimits(max_seconds=0.005), "audio_too_long"),
    ],
)
async def test_stream_input_limits_are_cumulative(values, limits, code):
    backend = Streaming()
    async with AsrService(backend, limits=limits) as service:
        with pytest.raises(InvalidAudio, match=code):
            async with service.stream(
                chunks(*values), sample_rate=16000, source_id="mic"
            ) as results:
                async for _ in results:
                    pass
        assert backend.cleaned


async def test_idle_input_timeout_and_source_exception_are_sanitized():
    async def idle():
        await asyncio.Event().wait()
        yield b"\0\0"

    async def failing():
        raise RuntimeError("private microphone information")
        yield

    for source, error, code in [
        (idle, AsrTimeout, "asr_timeout"),
        (failing, BackendError, "recognition_failed"),
    ]:
        backend = Streaming()
        async with AsrService(backend, timeout=0.03) as service:
            with pytest.raises(error, match=code):
                async with service.stream(source(), sample_rate=16000, source_id="mic") as results:
                    await anext(results)
            assert backend.cleaned


async def test_service_close_cancels_stream_even_when_consumer_is_idle():
    backend = Streaming()
    service = AsrService(backend)
    async with service.stream(
        chunks(*([b"\0\0"] * 100)), sample_rate=16000, source_id="mic"
    ) as results:
        await anext(results)
        await service.aclose()
        assert backend.cleaned
        with pytest.raises(asyncio.CancelledError):
            await anext(results)
    with pytest.raises(AsrClosed):
        async with service.stream(chunks(b"\0\0"), sample_rate=16000, source_id="mic"):
            pass


async def test_cancellation_token_stops_backpressured_stream():
    class Signal:
        cancelled = False

    signal = Signal()
    backend = Streaming()
    async with AsrService(backend) as service:
        async with service.stream(
            chunks(*([b"\0\0"] * 100)), sample_rate=16000, source_id="mic", cancellation=signal
        ) as results:
            await anext(results)
            signal.cancelled = True
            await asyncio.sleep(0.05)
            assert backend.cleaned
            with pytest.raises(asyncio.CancelledError):
                await anext(results)


@pytest.mark.parametrize(
    "outputs", [[("partial", False)], [("final", True), ("late", False)], [(None, False)]]
)
async def test_invalid_stream_backend_contract(outputs):
    class Invalid:
        async def stream(self, chunks, *, sample_rate):
            for output in outputs:
                yield output

    async with AsrService(Invalid()) as service:
        with pytest.raises(BackendError):
            async with service.stream(
                chunks(b"\0\0"), sample_rate=16000, source_id="mic"
            ) as results:
                async for _ in results:
                    pass
