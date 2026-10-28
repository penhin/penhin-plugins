"""Plugin-owned input and result contracts, independent of any host broker."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields

from .errors import MediaError

IMAGE_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
VIDEO_TYPES = {"video/mp4": "mov", "video/webm": "matroska,webm"}


@dataclass(frozen=True)
class MediaLimits:
    max_attachments: int = 8
    max_attachment_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_pixels: int = 20_000_000
    max_side: int = 1536
    max_pdf_pages: int = 16
    max_video_frames: int = 24
    max_decoded_frames: int = 7200
    max_parts: int = 32
    max_text_chars: int = 16_384
    max_output_chars: int = 65_536
    max_render_bytes: int = 32 * 1024 * 1024
    max_visual_bytes: int = 2 * 1024 * 1024
    max_video_seconds: float = 120.0
    frame_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name.endswith("seconds"):
                valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            else:
                valid = type(value) is int and value > 0
            if not valid:
                raise ValueError(f"{item.name} must be finite and positive")


@dataclass(frozen=True)
class MediaInput:
    data: bytes = field(repr=False)
    source_id: str
    mime_type: str
    # Hosts can also supply an already extracted frame under an authorized ID.
    timestamp_seconds: float | None = None

    def validate(self, limits: MediaLimits) -> None:
        if not valid_id(self.source_id) or not isinstance(self.data, bytes) or not self.data:
            raise MediaError("invalid_input")
        if len(self.data) > limits.max_attachment_bytes:
            raise MediaError("media_limit_exceeded")
        if not isinstance(
            self.mime_type, str
        ) or self.mime_type not in IMAGE_TYPES | VIDEO_TYPES | {"application/pdf": "PDF"}:
            raise MediaError("unsupported_media")
        if self.timestamp_seconds is not None:
            if (
                self.mime_type not in IMAGE_TYPES
                or type(self.timestamp_seconds) not in (int, float)
                or not math.isfinite(self.timestamp_seconds)
                or self.timestamp_seconds < 0
            ):
                raise MediaError("invalid_input")


def valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and not any(ord(char) < 32 for char in value)
    )


@dataclass(frozen=True)
class Visual:
    jpeg: bytes = field(repr=False)
    kind: str = "image"
    page: int | None = None
    timestamp_seconds: float | None = None
    extracted_text: str = field(default="", repr=False)


@dataclass(frozen=True)
class SupplementPart:
    source_id: str
    mime_type: str
    attachment_index: int
    part_index: int
    kind: str
    text: str = field(repr=False)
    extracted_text: str = field(default="", repr=False)
    page: int | None = None
    timestamp_seconds: float | None = None


@dataclass(frozen=True)
class ContextSupplement:
    parts: tuple[SupplementPart, ...] = field(repr=False)
    backend: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "type": "context_supplement",
            "trust": "untrusted",
            "plugin": "media_understanding",
            "version": "0.1.0",
            "backend": self.backend,
            "parts": [asdict(part) for part in self.parts],
            "warnings": list(self.warnings),
        }

    def as_text_block(self) -> dict:
        """Append to the originating USER message; never to Context.system.

        Labels describe provenance, not a prompt-injection security boundary.
        This method is explicit: no global mutable transform or repeated inference.
        """
        return {"type": "text", "text": json.dumps(self.as_dict(), ensure_ascii=True)}
