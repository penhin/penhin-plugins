"""Await owned cleanup even when a caller cancels more than once."""

import asyncio


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
