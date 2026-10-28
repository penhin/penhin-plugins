import asyncio

import pytest
from test_service import Immediate

from penhin_asr import (
    AsrBusy,
    AsrService,
    AsrTimeout,
    AudioInput,
    BackendError,
    InvalidAudio,
    Transcript,
    VoiceSubmission,
)


async def test_voice_preparation_separates_display_from_model_text(audio):
    backend = Immediate("  用户的语音内容  ")
    requested = []

    async def reader(source_id):
        requested.append(source_id)
        return audio

    async with AsrService(backend) as service:
        voice = await service.prepare_voice(audio.source_id, read_audio=reader)
    assert voice.model_text == "用户的语音内容"
    assert voice.as_display_message() == {
        "type": "audio",
        "source_id": audio.source_id,
        "duration_seconds": 0.05,
    }
    assert requested == [audio.source_id] and len(backend.calls) == 1
    assert voice.model_text not in repr(voice)
    assert voice.model_text not in str(voice.as_display_message())


@pytest.mark.parametrize("text", ["", "  \n\t"])
async def test_no_speech_stops_voice_submission_but_preview_can_be_empty(audio, text):
    async def reader(_):
        return audio

    async with AsrService(Immediate(text)) as service:
        with pytest.raises(InvalidAudio, match="^no_speech$"):
            await service.prepare_voice(audio.source_id, read_audio=reader)
        assert (await service.transcribe(audio)).text == ""


@pytest.mark.parametrize(
    "problem,code",
    [
        ("denied", "attachment_unavailable"),
        ("mismatched", "attachment_mismatch"),
        ("wrong_type", "attachment_mismatch"),
    ],
)
async def test_voice_reader_failure_and_source_binding(audio, problem, code):
    backend = Immediate()

    async def reader(_):
        if problem == "denied":
            raise PermissionError("private recording path")
        if problem == "mismatched":
            return AudioInput(audio.data, "another-session-recording")
        return b"private content"

    async with AsrService(backend) as service:
        with pytest.raises(BackendError, match=f"^{code}$"):
            await service.prepare_voice(audio.source_id, read_audio=reader)
    assert not backend.calls


@pytest.mark.parametrize("stop", ["cancel", "close", "timeout", "token"])
async def test_voice_reader_is_inside_service_lifecycle(audio, stop):
    class Signal:
        cancelled = False

    signal = Signal()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    backend = Immediate()
    cleaned = False

    async def reader(_):
        nonlocal cleaned
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned = True

    service = AsrService(backend, timeout=0.05 if stop == "timeout" else 60)
    task = asyncio.create_task(
        service.prepare_voice(audio.source_id, read_audio=reader, cancellation=signal)
    )
    await entered.wait()
    with pytest.raises(AsrBusy):
        await service.transcribe(audio)
    closing = None
    if stop == "cancel":
        task.cancel()
    elif stop == "close":
        closing = asyncio.create_task(service.aclose())
    elif stop == "token":
        signal.cancelled = True
    await asyncio.wait_for(cleaning.wait(), 1)
    if stop == "cancel":
        task.cancel()  # Repeated caller cancellation still waits for reader cleanup.
    assert not task.done()
    release.set()
    with pytest.raises(AsrTimeout if stop == "timeout" else asyncio.CancelledError):
        await task
    if closing:
        await closing
    await service.aclose()
    assert cleaned and not backend.calls


async def test_voice_precancellation_does_not_read_audio(audio):
    class Signal:
        cancelled = True

    async def reader(_):
        pytest.fail("must not read cancelled input")

    async with AsrService(Immediate()) as service:
        with pytest.raises(asyncio.CancelledError):
            await service.prepare_voice(audio.source_id, read_audio=reader, cancellation=Signal())


def test_voice_reuses_final_streaming_text_and_rejects_partial():
    partial = Transcript("可修订", "mic", 1.0, is_final=False)
    with pytest.raises(InvalidAudio, match="final_transcript_required"):
        VoiceSubmission.from_transcript(partial)
    final = Transcript("最终文本", "mic", 1.2, is_final=True)
    voice = VoiceSubmission.from_transcript(final)
    assert voice.model_text == final.text
    assert voice.as_display_message()["source_id"] == final.source_id


@pytest.mark.parametrize("source_id", ["", None, "x" * 257])
async def test_invalid_source_id_is_rejected_before_attachment_read(source_id):
    async def reader(_):
        pytest.fail("invalid ID must not reach host storage")

    async with AsrService(Immediate()) as service:
        with pytest.raises(InvalidAudio, match="invalid_source_id"):
            await service.prepare_voice(source_id, read_audio=reader)
