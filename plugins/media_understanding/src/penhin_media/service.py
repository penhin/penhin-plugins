"""One atomic, bounded, cancellable media submission per service."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from ._async import drain
from .errors import MediaError
from .types import (
    VIDEO_TYPES,
    ContextSupplement,
    MediaInput,
    MediaLimits,
    SupplementPart,
    Visual,
    valid_id,
)

AttachmentReader = Callable[[str], Awaitable[MediaInput]]
EnableProject = Callable[[], Awaitable[bool]]


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...


class VisionBackend(Protocol):
    """No logging of input/output; cancellation must complete normal cleanup.

    Backends are host-owned and may be shared. Each describe call owns its
    transient resources; MediaService never closes shared models or servers.
    """

    name: str

    async def describe(self, visual: Visual) -> str: ...


class Renderer(Protocol):
    async def render(self, media: MediaInput, limits: MediaLimits) -> tuple[Visual, ...]: ...


class MediaService:
    def __init__(
        self,
        backend: VisionBackend,
        *,
        renderer: Renderer | None = None,
        limits: MediaLimits | None = None,
        timeout: float = 180.0,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not valid_id(backend.name):
            raise ValueError("backend needs a bounded name")
        if renderer is None:
            from .renderer import LocalRenderer

            renderer = LocalRenderer()
        self._backend, self._renderer = backend, renderer
        self.limits, self.timeout = limits or MediaLimits(), timeout
        self._closed = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("MediaService belongs to another event loop")
        self._loop = loop
        return loop

    async def prepare_fallback(
        self,
        attachment_ids: Sequence[str],
        *,
        read_attachment: AttachmentReader,
        ensure_enabled: EnableProject,
        cancellation: CancellationSignal | None = None,
    ) -> ContextSupplement:
        """Host submission seam: enablement must succeed BEFORE reading media.

        The host callback checks installation/authorization and persisted project
        enablement, prompting only on first use. False blocks the whole submission.
        It must never silently enable a project. No Provider calls/retries occur here.
        """
        return await self._run(attachment_ids, read_attachment, ensure_enabled, cancellation)

    async def understand(
        self,
        attachment_ids: Sequence[str],
        *,
        read_attachment: AttachmentReader,
        cancellation: CancellationSignal | None = None,
    ) -> ContextSupplement:
        """Explicit invocation after host authorization (also used by the optional tool)."""
        return await self._run(attachment_ids, read_attachment, None, cancellation)

    async def _run(self, ids, reader, enable, cancellation) -> ContextSupplement:
        loop = self._bind()
        if self._closed:
            raise MediaError("media_closed")
        if self._task is not None:
            raise MediaError("media_busy")
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()
        if (
            not isinstance(ids, (list, tuple))
            or not ids
            or len(ids) > self.limits.max_attachments
            or not all(valid_id(x) for x in ids)
        ):
            raise MediaError("invalid_input")
        task = asyncio.create_task(self._process(tuple(ids), reader, enable))
        self._task = task
        deadline = loop.time() + self.timeout
        try:
            while not task.done():
                if cancellation is not None and cancellation.cancelled:
                    raise asyncio.CancelledError()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise MediaError("media_timeout")
                await asyncio.wait({task}, timeout=min(0.02, remaining))
            if self._closed or (cancellation is not None and cancellation.cancelled):
                raise asyncio.CancelledError()
            if loop.time() >= deadline:
                raise MediaError("media_timeout")
            try:
                return task.result()
            except MediaError as error:
                raise MediaError(error.code) from None
            except Exception:
                raise MediaError("backend_failed") from None
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()
            interrupted = await drain(task)
            if not task.cancelled():
                task.exception()
            self._task = None
            if interrupted:
                raise asyncio.CancelledError()

    async def _process(self, ids, reader, enable) -> ContextSupplement:
        if enable is not None:
            try:
                enabled = await enable()
            except Exception:
                raise MediaError("enablement_failed") from None
            if enabled is not True:
                raise MediaError("project_not_enabled")
        parts, warnings = [], []
        total_bytes = output_chars = 0
        for attachment_index, source_id in enumerate(ids):
            try:
                media = await reader(source_id)
            except Exception:
                raise MediaError("attachment_unavailable") from None
            if not isinstance(media, MediaInput) or media.source_id != source_id:
                raise MediaError("attachment_mismatch")
            media.validate(self.limits)
            total_bytes += len(media.data)
            if total_bytes > self.limits.max_total_bytes:
                raise MediaError("media_limit_exceeded")
            visuals = await self._renderer.render(media, self.limits)
            if not visuals or len(parts) + len(visuals) > self.limits.max_parts:
                raise MediaError("media_limit_exceeded")
            if media.mime_type in VIDEO_TYPES:
                warnings.extend(["video_frames_sampled", "video_audio_not_processed"])
            for part_index, visual in enumerate(visuals):
                self._validate_visual(visual)
                text = await self._backend.describe(visual)
                if not isinstance(text, str) or not text.strip():
                    raise MediaError("invalid_backend_result")
                output_chars += len(text) + len(visual.extracted_text)
                if (
                    len(text) > self.limits.max_text_chars
                    or output_chars > self.limits.max_output_chars
                ):
                    raise MediaError("output_limit_exceeded")
                parts.append(
                    SupplementPart(
                        source_id,
                        media.mime_type,
                        attachment_index,
                        part_index,
                        visual.kind,
                        text.strip(),
                        visual.extracted_text,
                        visual.page,
                        visual.timestamp_seconds,
                    )
                )
        return ContextSupplement(tuple(parts), self._backend.name, tuple(dict.fromkeys(warnings)))

    def _validate_visual(self, visual: Visual) -> None:
        if (
            not isinstance(visual, Visual)
            or not isinstance(visual.jpeg, bytes)
            or not visual.jpeg
            or len(visual.jpeg) > self.limits.max_visual_bytes
            or not isinstance(visual.extracted_text, str)
            or len(visual.extracted_text) > self.limits.max_text_chars
            or visual.kind not in {"image", "pdf_page", "video_frame"}
        ):
            raise MediaError("invalid_media")
        if visual.kind == "pdf_page" and (type(visual.page) is not int or visual.page < 1):
            raise MediaError("invalid_media")
        if visual.kind == "video_frame" and (
            type(visual.timestamp_seconds) not in (int, float)
            or not math.isfinite(visual.timestamp_seconds)
            or visual.timestamp_seconds < 0
        ):
            raise MediaError("invalid_media")

    async def aclose(self) -> None:
        self._bind()
        self._closed = True
        task = self._task
        if task is not None:
            if not task.done() and not task.cancelling():
                task.cancel()
            interrupted = await drain(task)
            if not task.cancelled():
                task.exception()
            if interrupted:
                raise asyncio.CancelledError()

    async def __aenter__(self) -> MediaService:
        self._bind()
        if self._closed:
            raise MediaError("media_closed")
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()
