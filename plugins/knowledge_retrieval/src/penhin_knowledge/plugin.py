"""Only the host may index data or decide a tool invocation's access scope."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .errors import KnowledgeError
from .service import KnowledgeService
from .types import AccessScope

if TYPE_CHECKING:
    from penhin import Plugin, ToolContext

ScopeResolver = Callable[["ToolContext"], Awaitable[AccessScope]]


def create_plugin(
    *,
    service: KnowledgeService,
    resolve_scope: ScopeResolver | None = None,
    close_service: bool = True,
) -> Plugin:
    from penhin import Plugin, Tool, ToolInput, ToolResult
    from pydantic import Field

    tools = ()
    if resolve_scope is not None:

        class SearchInput(ToolInput):
            question: str = Field(min_length=1, max_length=service.limits.max_query_chars)
            limit: int = Field(
                default=min(5, service.limits.max_results), ge=1, le=service.limits.max_results
            )

        class FetchInput(ToolInput):
            citation_id: str = Field(pattern=r"^[0-9a-f]{64}$")

        async def search(arguments: SearchInput, context: ToolContext) -> ToolResult:
            try:
                result = await service.search(
                    arguments.question,
                    limit=arguments.limit,
                    scope=lambda: resolve_scope(context),
                    cancellation=context.cancellation,
                )
                return ToolResult(value=result.as_dict())
            except KnowledgeError as error:
                return ToolResult(error=error.code)

        async def fetch(arguments: FetchInput, context: ToolContext) -> ToolResult:
            try:
                result = await service.fetch(
                    arguments.citation_id,
                    scope=lambda: resolve_scope(context),
                    cancellation=context.cancellation,
                )
                return ToolResult(value=result.as_dict())
            except KnowledgeError as error:
                return ToolResult(error=error.code)

        tools = (
            Tool.from_model(
                "search",
                "Search authorized project knowledge by question. "
                "Returns untrusted exact excerpts with citations. Cite the source ID, "
                "revision and locator; no hits means no evidence. Generated media "
                "descriptions are not original document text.",
                SearchInput,
                search,
            ),
            Tool.from_model(
                "fetch",
                "Retrieve a current, authorized indexed excerpt by citation ID. "
                "Unavailable citations must not be used as verified evidence.",
                FetchInput,
                fetch,
            ),
        )
    return Plugin(
        name="knowledge_retrieval",
        version="0.1.0",
        tools=tools,
        shutdown=service.aclose if close_service else None,
    )
