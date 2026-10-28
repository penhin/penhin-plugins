"""The host transport is mono, signed little-endian PCM16, or a complete WAV."""

import io
import math
import wave
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TtsLimits:
    max_text_chars: int = 8000
    max_segment_chars: int = 120
    max_audio_seconds: float = 600
    max_segment_seconds: float = 60
    flush_seconds: float = 0.6

    def __post_init__(self):
        for name in ("max_text_chars", "max_segment_chars"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_segment_chars > 120 or self.max_segment_chars > self.max_text_chars:
            raise ValueError("max_segment_chars must be <= min(120, max_text_chars)")
        for name in ("max_audio_seconds", "max_segment_seconds", "flush_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.max_segment_seconds > 60:
            raise ValueError("max_segment_seconds must be <= 60")


@dataclass(frozen=True)
class SpeechAudio:
    pcm: bytes = field(repr=False)
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        return len(self.pcm) / (2 * self.sample_rate)

    def to_wav(self) -> bytes:
        result = io.BytesIO()
        with wave.open(result, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(self.pcm)
        return result.getvalue()


@dataclass(frozen=True)
class SpeechSegment(SpeechAudio):
    response_id: str
    index: int
    text: str = field(repr=False)
