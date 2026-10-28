import asyncio
import struct

import pytest
from conftest import wav_bytes

from penhin_asr import (
    AsrBusy,
    AsrClosed,
    AsrService,
    AsrTimeout,
    AudioInput,
    AudioLimits,
    BackendError,
    InvalidAudio,
)


class Immediate:
    def __init__(self, text="你好，世界。"):
        self.text = text
        self.calls = []

    async def transcribe(self, audio):
        self.calls.append(audio)
        return self.text


def odd_sample_wav():
    data = bytearray(wav_bytes())
    data.append(0)
    struct.pack_into("<I", data, 4, len(data) - 8)
    struct.pack_into("<I", data, 40, len(data) - 44)
    return bytes(data)


class Blocking:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = False

    async def transcribe(self, audio):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release.wait()
            self.cleaned = True


async def test_direct_call_returns_final_without_loading_penhin(audio):
    backend = Immediate()
    async with AsrService(backend) as service:
        result = await service.transcribe(audio)
        assert result.as_dict() == {
            "text": "你好，世界。",
            "source_id": "attachment-1",
            "duration_seconds": 0.05,
            "is_final": True,
        }
        assert backend.calls[0].sample_rate == 16000
        assert len(backend.calls[0].samples) == 1600
        assert result.text not in repr(result)
        assert "data=" not in repr(audio)
    with pytest.raises(AsrClosed):
        await service.transcribe(audio)


@pytest.mark.parametrize(
    "data,code",
    [
        (b"not a WAV", "invalid_wav"),
        (wav_bytes(channels=2), "expected_mono_pcm16"),
        (wav_bytes(width=1), "expected_mono_pcm16"),
        (wav_bytes(rate=4000), "unsupported_sample_rate"),
        (wav_bytes(seconds=0), "empty_audio"),
        (wav_bytes()[:-1], "truncated_wav"),
        (odd_sample_wav(), "truncated_wav"),
    ],
)
async def test_rejects_invalid_audio_before_backend(data, code):
    backend = Immediate()
    async with AsrService(backend) as service:
        with pytest.raises(InvalidAudio, match=code):
            await service.transcribe(AudioInput(data, "attachment"))
    assert backend.calls == []


@pytest.mark.parametrize(
    "limits,code",
    [
        (AudioLimits(max_bytes=100), "audio_too_large"),
        (AudioLimits(max_seconds=0.01), "audio_too_long"),
    ],
)
async def test_enforces_host_limits_before_backend(audio, limits, code):
    backend = Immediate()
    async with AsrService(backend, limits=limits) as service:
        with pytest.raises(InvalidAudio, match=code):
            await service.transcribe(audio)
    assert not backend.calls


async def test_repeated_cancellation_keeps_service_busy_until_backend_cleanup(audio):
    backend = Blocking()
    service = AsrService(backend)
    task = asyncio.create_task(service.transcribe(audio))
    await backend.entered.wait()
    task.cancel()
    await backend.cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(AsrBusy):
        await service.transcribe(audio)
    backend.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.cleaned
    await service.aclose()


async def test_close_cancels_direct_call_and_drains_even_if_close_is_cancelled(audio):
    backend = Blocking()
    service = AsrService(backend)
    task = asyncio.create_task(service.transcribe(audio))
    await backend.entered.wait()
    closing = asyncio.create_task(service.aclose())
    await backend.cleaning.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    assert not closing.done()
    with pytest.raises(AsrClosed):
        await service.transcribe(audio)
    backend.release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.cleaned
    await service.aclose()


async def test_timeout_waits_for_cleanup(audio):
    backend = Blocking()
    service = AsrService(backend, timeout=0.03)
    task = asyncio.create_task(service.transcribe(audio))
    await backend.cleaning.wait()
    assert not task.done()
    backend.release.set()
    with pytest.raises(AsrTimeout):
        await task
    assert backend.cleaned
    await service.aclose()


async def test_token_cancellation_rejects_late_result(audio):
    class Signal:
        cancelled = False

    signal = Signal()

    class Late:
        async def transcribe(self, pcm):
            signal.cancelled = True
            return "late secret"

    async with AsrService(Late()) as service:
        with pytest.raises(asyncio.CancelledError):
            await service.transcribe(audio, cancellation=signal)


async def test_precancelled_call_never_runs_backend(audio):
    class Signal:
        cancelled = True

    backend = Immediate()
    async with AsrService(backend) as service:
        with pytest.raises(asyncio.CancelledError):
            await service.transcribe(audio, cancellation=Signal())
    assert not backend.calls


@pytest.mark.parametrize("value", [None, b"text", "x" * 16385])
async def test_invalid_results_are_rejected(audio, value):
    async with AsrService(Immediate(value)) as service:
        with pytest.raises(BackendError, match="invalid_backend_result"):
            await service.transcribe(audio)


async def test_backend_errors_do_not_expose_private_data(audio):
    class Failing:
        async def transcribe(self, pcm):
            raise RuntimeError("private recording /host/secret.wav")

    async with AsrService(Failing()) as service:
        with pytest.raises(BackendError) as caught:
            await service.transcribe(audio)
    assert str(caught.value) == "recognition_failed"
    assert caught.value.__suppress_context__


async def test_empty_transcript_is_valid_no_speech(audio):
    async with AsrService(Immediate("  ")) as service:
        assert (await service.transcribe(audio)).text == ""
