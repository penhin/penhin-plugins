"""Bounded host-owned audio and cumulative transcription snapshots."""

from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass, field

from .errors import InvalidAudio


@dataclass(frozen=True)
class AudioInput:
    """WAV bytes supplied by the host, identified by its opaque attachment ID."""

    data: bytes = field(repr=False)
    source_id: str


@dataclass(frozen=True)
class AudioLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_seconds: float = 120.0

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 10 * 1024 * 1024:
            raise ValueError("max_bytes must be within 1..10485760")
        if not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 120:
            raise ValueError("max_seconds must be within (0, 120]")


@dataclass(frozen=True)
class PcmAudio:
    """Validated little-endian signed PCM16, mono; backend input only."""

    samples: bytes = field(repr=False)
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True)
class Transcript:
    text: str = field(repr=False)
    source_id: str
    duration_seconds: float
    is_final: bool = True

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "source_id": self.source_id,
            "duration_seconds": self.duration_seconds,
            "is_final": self.is_final,
        }


@dataclass(frozen=True)
class VoiceSubmission:
    """Separate host-visible audio metadata from the model's user text.

    This is a prepared submission, not proof of delivery or an attachment store.
    Hosts own the original audio, authorization, persistence and message IDs.
    """

    source_id: str
    duration_seconds: float
    model_text: str = field(repr=False)

    @classmethod
    def from_transcript(cls, transcript: Transcript) -> VoiceSubmission:
        """Reuse a final streaming result without another recognition pass."""
        if not isinstance(transcript, Transcript) or transcript.is_final is not True:
            raise InvalidAudio("final_transcript_required")
        if (
            not isinstance(transcript.text, str)
            or len(transcript.text) > 16384
            or not isinstance(transcript.source_id, str)
            or not 1 <= len(transcript.source_id) <= 256
            or type(transcript.duration_seconds) not in (int, float)
            or not math.isfinite(transcript.duration_seconds)
            or transcript.duration_seconds <= 0
        ):
            raise InvalidAudio("invalid_transcript")
        text = transcript.text.strip()
        if not text:
            raise InvalidAudio("no_speech")
        return cls(transcript.source_id, transcript.duration_seconds, text)

    def as_display_message(self) -> dict:
        """Audio-only presentation data; deliberately contains no transcript."""
        return {
            "type": "audio",
            "source_id": self.source_id,
            "duration_seconds": self.duration_seconds,
        }


def decode_wav(audio: AudioInput, limits: AudioLimits) -> PcmAudio:
    if not isinstance(audio, AudioInput) or not isinstance(audio.data, bytes):
        raise InvalidAudio("expected_wav_bytes")
    if not isinstance(audio.source_id, str) or not 1 <= len(audio.source_id) <= 256:
        raise InvalidAudio("invalid_source_id")
    if len(audio.data) > limits.max_bytes:
        raise InvalidAudio("audio_too_large")
    try:
        with wave.open(io.BytesIO(audio.data), "rb") as stream:
            if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
                raise InvalidAudio("expected_mono_pcm16")
            if stream.getcomptype() != "NONE":
                raise InvalidAudio("expected_uncompressed_wav")
            rate, frames = stream.getframerate(), stream.getnframes()
            if not 8000 <= rate <= 48000:
                raise InvalidAudio("unsupported_sample_rate")
            if not frames:
                raise InvalidAudio("empty_audio")
            duration = frames / rate
            if duration > limits.max_seconds:
                raise InvalidAudio("audio_too_long")
            # One extra frame also detects an odd/truncated final PCM sample.
            samples = stream.readframes(frames + 1)
            if len(samples) != frames * 2:
                raise InvalidAudio("truncated_wav")
    except (wave.Error, EOFError, OSError, ValueError):
        raise InvalidAudio("invalid_wav") from None
    return PcmAudio(samples, rate, duration)
