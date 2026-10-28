"""Bounded local indexing and retrieval, independent of Penhin and model APIs."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from ._index import Index, query_expression
from .errors import KnowledgeError
from .types import (
    AccessScope,
    IndexResult,
    KnowledgeLimits,
    KnowledgeSource,
    SearchHit,
    SearchResult,
    valid_id,
)

ScopeReader = Callable[[], Awaitable[AccessScope]]


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...


async def _drain(task: asyncio.Task) -> bool:
    owner = asyncio.current_task()
    count = owner.cancelling() if owner else 0
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = owner.cancelling() if owner else 0
            if current > count:
                interrupted, count = True, current
        except Exception:
            break
    if not task.cancelled():
        task.exception()
    return interrupted


class KnowledgeService:
    def __init__(
        self, database: str | Path, *, limits: KnowledgeLimits | None = None, timeout: float = 30.0
    ) -> None:
        if str(database) == ":memory:":
            raise ValueError("A persistent database path is required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.limits, self.timeout = limits or KnowledgeLimits(), timeout
        self._index = Index(Path(database).resolve(), self.limits)
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("KnowledgeService belongs to another event loop")
        self._loop = loop
        return loop

    async def upsert(
        self,
        project_id: str,
        sources: Sequence[KnowledgeSource],
        *,
        cancellation: CancellationSignal | None = None,
    ) -> IndexResult:
        """Atomically replace these source IDs; unrelated sources remain indexed."""
        if not valid_id(project_id) or not isinstance(sources, (list, tuple)) or not sources:
            raise KnowledgeError("invalid_source")
        if len(sources) > self.limits.max_sources:
            raise KnowledgeError("index_limit_exceeded")
        for source in sources:
            if not isinstance(source, KnowledgeSource):
                raise KnowledgeError("invalid_source")
            source.validate(self.limits)
        if len({s.source_id for s in sources}) != len(sources):
            raise KnowledgeError("duplicate_source")
        if sum(len(p.text) for s in sources for p in s.parts) > self.limits.max_batch_chars:
            raise KnowledgeError("index_limit_exceeded")
        return await self._run(self._index.upsert, (project_id, tuple(sources)), cancellation)

    async def remove(
        self, project_id: str, source_id: str, *, cancellation: CancellationSignal | None = None
    ) -> bool:
        if not valid_id(project_id) or not valid_id(source_id):
            raise KnowledgeError("invalid_source")
        return await self._run(self._index.remove, (project_id, source_id), cancellation)

    async def search(
        self,
        question: str,
        *,
        scope: AccessScope | ScopeReader,
        limit: int = 5,
        cancellation: CancellationSignal | None = None,
    ) -> SearchResult:
        expression = query_expression(question, self.limits)
        if not isinstance(scope, AccessScope) and not callable(scope):
            raise KnowledgeError("invalid_scope")
        if type(limit) is not int or not 1 <= limit <= self.limits.max_results:
            raise KnowledgeError("invalid_limit")
        return await self._run(self._index.search, (expression,), cancellation, scope, (limit,))

    async def fetch(
        self,
        citation_id: str,
        *,
        scope: AccessScope | ScopeReader,
        cancellation: CancellationSignal | None = None,
    ) -> SearchHit:
        if not isinstance(citation_id, str) or not re.fullmatch(r"[0-9a-f]{64}", citation_id):
            raise KnowledgeError("citation_unavailable")
        if not isinstance(scope, AccessScope) and not callable(scope):
            raise KnowledgeError("invalid_scope")
        return await self._run(self._index.fetch, (citation_id,), cancellation, scope)

    async def _process(self, function, args, scope, tail):
        if scope is not None:
            if callable(scope):
                try:
                    scope = await scope()
                except Exception:
                    raise KnowledgeError("access_denied") from None
            if not isinstance(scope, AccessScope):
                raise KnowledgeError("invalid_scope")
            scope.validate(self.limits)
            args = (*args, scope, *tail)
        stop = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(function, *args, stop))
        try:
            return await asyncio.shield(worker)
        finally:
            stop.set()
            if await _drain(worker):
                raise asyncio.CancelledError()

    async def _run(self, function, args, cancellation, scope=None, tail=()):
        loop = self._bind()
        if self._closed:
            raise KnowledgeError("knowledge_closed")
        if self._task is not None:
            raise KnowledgeError("knowledge_busy")
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()
        task = asyncio.create_task(self._process(function, args, scope, tail))
        self._task = task
        deadline = loop.time() + self.timeout
        try:
            while not task.done():
                if cancellation is not None and cancellation.cancelled:
                    raise asyncio.CancelledError()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise KnowledgeError("knowledge_timeout")
                await asyncio.wait({task}, timeout=min(0.02, remaining))
            if self._closed or (cancellation is not None and cancellation.cancelled):
                raise asyncio.CancelledError()
            if loop.time() >= deadline:
                raise KnowledgeError("knowledge_timeout")
            return task.result()
        except KnowledgeError as error:
            raise KnowledgeError(error.code) from None
        except sqlite3.OperationalError as error:
            code = "index_busy" if "locked" in str(error) else "index_unavailable"
            raise KnowledgeError(code) from None
        except Exception:
            raise KnowledgeError("index_unavailable") from None
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()
            interrupted = await _drain(task)
            self._task = None
            if interrupted:
                raise asyncio.CancelledError()

    async def aclose(self) -> None:
        self._bind()
        self._closed = True
        task = self._task
        if task is not None:
            if not task.done() and not task.cancelling():
                task.cancel()
            if await _drain(task):
                raise asyncio.CancelledError()

    async def __aenter__(self) -> KnowledgeService:
        self._bind()
        if self._closed:
            raise KnowledgeError("knowledge_closed")
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()
