import asyncio
import threading
from dataclasses import replace

import pytest

from penhin_knowledge import KnowledgeError, KnowledgeService, SourcePart


class Cancellation:
    cancelled = False


async def test_scope_reader_timeout_and_errors_are_bounded(service, scope):
    cleaned = asyncio.Event()

    async def blocking():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    service.timeout = 0.03
    with pytest.raises(KnowledgeError, match="^knowledge_timeout$"):
        await service.search("query", scope=blocking)
    assert cleaned.is_set()

    async def denied():
        raise PermissionError("secret project and database path")

    with pytest.raises(KnowledgeError, match="^access_denied$"):
        await service.search("query", scope=denied)
    service.timeout = 30
    assert not (await service.search("query", scope=scope)).hits


@pytest.mark.parametrize("stop_kind", ["token", "task", "close", "timeout"])
async def test_worker_cancel_rolls_back_and_joins_before_return(
    service,
    source,
    scope,
    monkeypatch,
    stop_kind,
):
    await service.upsert("project", [source])
    entered, release = threading.Event(), threading.Event()
    original = service._index._delete

    def blocking(connection, project_id, source_id):
        result = original(connection, project_id, source_id)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(service._index, "_delete", blocking)
    token = Cancellation()
    if stop_kind == "timeout":
        service.timeout = 0.1
    task = asyncio.create_task(
        service.upsert(
            "project",
            [replace(source, parts=(SourcePart("replacement text"),))],
            cancellation=token,
        )
    )
    assert await asyncio.to_thread(entered.wait, 2)
    closing = None
    try:
        with pytest.raises(KnowledgeError, match="knowledge_busy"):
            await service.search("备份", scope=scope)
        if stop_kind == "token":
            token.cancelled = True
        elif stop_kind == "task":
            task.cancel()
        elif stop_kind == "close":
            closing = asyncio.create_task(service.aclose())
        await asyncio.sleep(0.15)
        assert not task.done()
        if closing:
            assert not closing.done()
        if stop_kind == "task":
            task.cancel()  # repeated cancellation must still join the worker
    finally:
        release.set()
    with pytest.raises(KnowledgeError if stop_kind == "timeout" else asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    if closing:
        await closing
    async with KnowledgeService(service._index.path) as reopened:
        assert (await reopened.search("备份", scope=scope)).hits[0].text == source.parts[0].text
        assert not (await reopened.search("replacement", scope=scope)).hits


async def test_cancelled_token_never_calls_authorizer(service):
    async def unexpected():
        pytest.fail("must not authorize a cancelled call")

    token = Cancellation()
    token.cancelled = True
    with pytest.raises(asyncio.CancelledError):
        await service.search("q", scope=unexpected, cancellation=token)


async def test_closed_service_rejects_requests(service, scope):
    await service.aclose()
    await service.aclose()
    with pytest.raises(KnowledgeError, match="^knowledge_closed$"):
        await service.search("q", scope=scope)
