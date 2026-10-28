"""Optional Agent tool stores generated audio using a host capability."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ._async import cancel_and_wait, watch
from .audio import SpeechAudio
from .errors import TtsError
from .service import TtsService

if TYPE_CHECKING:
    from penhin import Plugin, ToolContext

AudioPublisher = Callable[[SpeechAudio, "ToolContext"], Awaitable[str]]


def create_plugin(
    *,
    service: TtsService,
    publish_audio: AudioPublisher | None = None,
    player=None,
    close_service: bool = True,
) -> Plugin:
    """Without publish_audio register lifecycle only; direct host speech is preferred.

    A publisher stores a WAV under the ToolContext's session permissions and
    returns an attachment ID. It must be cancellable and clean partial writes.
    The model never supplies a filesystem path or triggers device playback.
    """
    from penhin import Plugin, Tool, ToolInput, ToolResult
    from pydantic import Field

    if player is not None and player.service is not service:
        raise ValueError("player must use the supplied service")
    tools = ()
    if publish_audio is not None:

        class SynthesizeInput(ToolInput):
            text: str = Field(min_length=1, max_length=service.limits.max_text_chars)
            speed: float = Field(default=1.0, ge=0.5, le=2.0)

        async def synthesize(arguments: SynthesizeInput, context: ToolContext):
            context.cancellation.check()
            try:
                audio = await service.synthesize(
                    arguments.text, speed=arguments.speed, cancellation=context.cancellation
                )
                context.cancellation.check()
                publishing = asyncio.create_task(publish_audio(audio, context))
                watcher = asyncio.create_task(watch(publishing, context.cancellation))
                try:
                    attachment_id = await asyncio.wait_for(
                        asyncio.shield(publishing), service.timeout
                    )
                finally:
                    try:
                        await cancel_and_wait(publishing)
                    finally:
                        await cancel_and_wait(watcher)
                context.cancellation.check()
                if not isinstance(attachment_id, str) or not 1 <= len(attachment_id) <= 256:
                    return ToolResult(error="invalid_attachment_id")
            except TtsError as error:
                return ToolResult(error=error.code)
            except asyncio.CancelledError:
                raise
            except Exception:
                return ToolResult(error="audio_publish_failed")
            return ToolResult(
                value={
                    "attachment_id": attachment_id,
                    "mime_type": "audio/wav",
                    "sample_rate": audio.sample_rate,
                    "duration_seconds": audio.duration_seconds,
                }
            )

        tools = (
            Tool.from_model(
                "synthesize",
                "Convert text to a speech attachment. Does not play audio.",
                SynthesizeInput,
                synthesize,
            ),
        )
    return Plugin(
        name="tts",
        version="0.1.0",
        tools=tools,
        shutdown=(player.aclose if player else service.aclose) if close_service else None,
    )
