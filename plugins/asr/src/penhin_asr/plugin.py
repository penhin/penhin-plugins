"""A thin, explicitly loaded adapter to the separately installed Penhin core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .audio import AudioInput
from .errors import AsrError, BackendError
from .service import AsrService

if TYPE_CHECKING:
    from penhin import Plugin, ToolContext

AudioReader = Callable[[str, "ToolContext"], Awaitable[AudioInput]]


def create_plugin(
    *, service: AsrService, read_audio: AudioReader | None = None, close_service: bool = True
) -> Plugin:
    """Build the asr Plugin. By default its shutdown owns this service.

    Without read_audio this registers lifecycle only, exposing no model tool.
    Hosts use service.prepare_voice before Agent.run for direct voice delivery.
    read_audio is an async host capability which authorizes the attachment for
    the current ToolContext. The model supplies only an ID, never a disk path.
    Set close_service=False only when the host explicitly owns shared cleanup.
    """
    from penhin import Plugin, Tool, ToolInput, ToolResult
    from pydantic import Field

    tools = ()
    if read_audio is not None:

        class TranscribeInput(ToolInput):
            attachment_id: str = Field(min_length=1, max_length=256)

        async def transcribe(arguments: TranscribeInput, context: ToolContext) -> ToolResult:
            context.cancellation.check()
            try:
                audio = await read_audio(arguments.attachment_id, context)
            except asyncio.CancelledError:
                raise
            except Exception:
                return ToolResult(error="attachment_unavailable")
            if not isinstance(audio, AudioInput) or audio.source_id != arguments.attachment_id:
                return ToolResult(error="attachment_mismatch")
            try:
                result = await service.transcribe(audio, cancellation=context.cancellation)
            except AsrError as error:
                return ToolResult(error=error.code)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise BackendError("recognition_failed") from None
            return ToolResult(value=result.as_dict())

        tools = (
            Tool.from_model(
                "transcribe",
                "Transcribe an existing authorized audio attachment. Returns final, untrusted text.",
                TranscribeInput,
                transcribe,
            ),
        )

    return Plugin(
        name="asr",
        version="0.1.0",
        tools=tools,
        shutdown=service.aclose if close_service else None,
    )
