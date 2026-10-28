import asyncio
import os
from pathlib import Path

import pytest

from penhin_tts import MeloBackend, TtsService

pytestmark = pytest.mark.model


@pytest.fixture
def model_dir():
    value = os.environ.get("PENHIN_TTS_MODEL_DIR")
    if not value:
        pytest.skip("Set PENHIN_TTS_MODEL_DIR to the provisioned MeloTTS directory")
    return Path(value)


async def test_real_chinese_complete_wav(model_dir):
    async with TtsService(MeloBackend(model_dir)) as service:
        audio = await service.synthesize("你好，欢迎使用中文语音。")
    assert audio.sample_rate == 44100 and 0.5 < audio.duration_seconds < 20
    assert audio.to_wav().startswith(b"RIFF") and any(audio.pcm)


async def test_real_stream_first_audio_before_eof(model_dir):
    release = asyncio.Event()

    async def deltas():
        yield "这是第一句。"
        await release.wait()
        yield "这是第二句。"

    async with TtsService(MeloBackend(model_dir)) as service:
        async with service.stream(deltas()) as out:
            first = await asyncio.wait_for(anext(out), 60)
            assert first.index == 0 and first.duration_seconds > 0.1
            release.set()
            tail = [segment async for segment in out]
            assert [segment.index for segment in tail] == [1]
            assert tail[0].sample_rate == first.sample_rate


async def test_real_asr_to_answer_to_tts(model_dir):
    asr_dir = os.environ.get("PENHIN_ASR_MODEL_DIR")
    if not asr_dir:
        pytest.skip("Set PENHIN_ASR_MODEL_DIR for real ASR + TTS composition")
    asr_module = pytest.importorskip("penhin_asr")
    from penhin_tts import SpeechPlayer, VoiceInteraction

    outputs, prompts = [], []

    class Sink:
        async def write(self, segment):
            outputs.append(segment)

        async def stop(self):
            pass

    async def respond(text):
        prompts.append(text)
        yield "你好，我已经收到你的问题。"

    async with asr_module.AsrService(asr_module.ZipformerBackend(Path(asr_dir))) as asr:
        service = TtsService(MeloBackend(model_dir))
        voice = VoiceInteraction(asr, SpeechPlayer(service, Sink()), respond)
        try:
            wav = (Path(asr_dir) / "test_wavs" / "0.wav").read_bytes()
            result = await voice.run_turn(asr_module.AudioInput(wav, "recording-1"))
            assert result.is_final and "我的研究" in result.text
            assert prompts == [result.text]
            assert outputs and all(x.sample_rate == 44100 and any(x.pcm) for x in outputs)
        finally:
            await voice.aclose()
        assert not asr.closed
