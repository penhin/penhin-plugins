"""Cancellation helpers: keep ownership until cleanup has actually finished."""

import asyncio
from typing import Protocol


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...


async def drain(task: asyncio.Task) -> bool:
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
    return interrupted


async def cancel_and_wait(task: asyncio.Task) -> None:
    if not task.done() and not task.cancelling():
        task.cancel()
    interrupted = await drain(task)
    if not task.cancelled():
        task.exception()
    if interrupted:
        raise asyncio.CancelledError()


async def watch(task: asyncio.Task, signal: CancellationSignal) -> None:
    while not task.done():
        if signal.cancelled:
            task.cancel()
            return
        await asyncio.sleep(0.02)
