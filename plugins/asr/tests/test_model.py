import asyncio
import os
from pathlib import Path

import pytest

from penhin_asr import AsrService, AudioInput, AudioLimits, ZipformerBackend
from penhin_asr.audio import decode_wav

pytestmark = pytest.mark.model


@pytest.fixture
def model_dir():
    directory = os.environ.get("PENHIN_ASR_MODEL_DIR")
    if not directory:
        pytest.skip("Set PENHIN_ASR_MODEL_DIR for real model verification")
    return Path(directory)


async def test_official_chinese_sample(model_dir):
    # Upstream Zipformer guide's published output for test_wavs/0.wav.
    # This verifies integration, not general recognition accuracy.
    audio = AudioInput((model_dir / "test_wavs/0.wav").read_bytes(), "sample-0")

    async def reader(_):
        return audio

    async with AsrService(ZipformerBackend(model_dir), timeout=90) as service:
        voice = await service.prepare_voice(audio.source_id, read_audio=reader)
    assert voice.model_text == "对我做了介绍啊那么我想说的是呢大家如果对我的研究感兴趣呢"
    assert "text" not in voice.as_display_message()


@pytest.mark.parametrize("sample,chunk_ms", [("0", 20), ("1", 320), ("8k", 100)])
async def test_live_partials_precede_eof_and_match_whole_file(model_dir, sample, chunk_ms):
    audio = AudioInput((model_dir / f"test_wavs/{sample}.wav").read_bytes(), sample)
    pcm = decode_wav(audio, AudioLimits())
    first_text = asyncio.Event()
    eof = False

    async def source():
        nonlocal eof
        size = pcm.sample_rate * chunk_ms // 1000 * 2
        for offset in range(0, len(pcm.samples), size):
            yield pcm.samples[offset : offset + size]
        # A backend buffering the whole input deadlocks here and fails the test.
        await asyncio.wait_for(first_text.wait(), 5)
        eof = True

    async with AsrService(ZipformerBackend(model_dir), timeout=90) as service:
        whole = await service.transcribe(audio)
        updates = []
        async with service.stream(
            source(), sample_rate=pcm.sample_rate, source_id=sample
        ) as results:
            async for update in results:
                updates.append(update)
                if update.text and not update.is_final:
                    assert not eof or first_text.is_set()
                    first_text.set()
        assert first_text.is_set()
        assert updates[-1].text == whole.text
        assert updates[-1].duration_seconds == pcm.duration_seconds
        assert sum(update.is_final for update in updates) == 1
        assert updates[-1].is_final and eof
