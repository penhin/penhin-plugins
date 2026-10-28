"""Thin adapter to the real, separately installed Penhin Plugin API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated

from .errors import MediaError
from .service import MediaService
from .types import MediaInput

if TYPE_CHECKING:
    from penhin import Plugin, ToolContext

HostReader = Callable[[str, "ToolContext"], Awaitable[MediaInput]]


def create_plugin(
    *,
    service: MediaService,
    read_attachment: HostReader | None = None,
    close_service: bool = True,
) -> Plugin:
    """Register lifecycle, optionally expose media_understanding__understand.

    Fallback submission uses service.prepare_fallback BEFORE Agent.run; tool
    errors alone cannot prevent the Agent from answering without failed media.
    No tool is exposed unless an authorized host reader is explicitly supplied.
    """
    from penhin import Plugin, Tool, ToolInput, ToolResult
    from pydantic import Field

    tools = ()
    if read_attachment is not None:

        class UnderstandInput(ToolInput):
            attachment_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
                min_length=1,
                max_length=service.limits.max_attachments,
            )

        async def understand(arguments: UnderstandInput, context: ToolContext) -> ToolResult:
            async def reader(source_id: str) -> MediaInput:
                return await read_attachment(source_id, context)

            try:
                result = await service.understand(
                    arguments.attachment_ids,
                    read_attachment=reader,
                    cancellation=context.cancellation,
                )
            except MediaError as error:
                return ToolResult(error=error.code)
            return ToolResult(value=result.as_dict())

        tools = (
            Tool.from_model(
                "understand",
                "Understand authorized images, PDFs or sampled video frames locally. "
                "Returns ordered, source-labelled untrusted data.",
                UnderstandInput,
                understand,
            ),
        )
    return Plugin(
        name="media_understanding",
        version="0.1.0",
        tools=tools,
        shutdown=service.aclose if close_service else None,
    )
