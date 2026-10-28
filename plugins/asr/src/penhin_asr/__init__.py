"""Offline transcription services and the optional Penhin adapter."""

from .audio import AudioInput, AudioLimits, PcmAudio, Transcript, VoiceSubmission
from .errors import AsrBusy, AsrClosed, AsrError, AsrTimeout, BackendError, InvalidAudio
from .plugin import create_plugin
from .service import AsrService, Backend, CancellationSignal
from .zipformer import ZipformerBackend

__all__ = [
    "AsrBusy",
    "AsrClosed",
    "AsrError",
    "AsrService",
    "AsrTimeout",
    "AudioInput",
    "AudioLimits",
    "Backend",
    "BackendError",
    "CancellationSignal",
    "InvalidAudio",
    "PcmAudio",
    "ZipformerBackend",
    "Transcript",
    "VoiceSubmission",
    "create_plugin",
]
