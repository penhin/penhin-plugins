import io
import wave

import pytest

from penhin_asr import AudioInput


def wav_bytes(*, seconds=0.05, rate=16000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\0" * int(rate * seconds) * channels * width)
    return output.getvalue()


@pytest.fixture
def audio():
    return AudioInput(wav_bytes(), "attachment-1")
