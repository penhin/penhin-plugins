"""A player owns interruption of both synthesis and the host's playback queue."""

import asyncio
from typing import Protocol

from ._async import cancel_and_wait, drain
from .audio import SpeechSegment
from .errors import TtsBusy, TtsClosed
from .service import TtsService


class AudioSink(Protocol):
    """write waits until played; stop aborts audible audio and clears all queued data.

    Implementations must be cancellable, must not retain stale writes after stop,
    and belong exclusively to one player. Remote sinks must acknowledge stop.
    """

    async def write(self, segment: SpeechSegment) -> None: ...
    async def stop(self) -> None: ...


class SpeechPlayer:
    def __init__(self, service: TtsService, sink: AudioSink):
        self.service, self.sink = service, sink
        self._task = None
        self._stopping = None
        self._idle = asyncio.Event()
        self._idle.set()
        self.closed = False

    async def speak(self, text, *, response_id="speech", speed=1.0, cancellation=None):
        if self.closed:
            raise TtsClosed()
        if self._task is not None:
            raise TtsBusy()
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()

        async def play():
            async with self.service.stream(
                text, response_id=response_id, speed=speed, cancellation=cancellation
            ) as output:
                async for segment in output:
                    await self.sink.write(segment)

        task = asyncio.create_task(play())
        self._task = task
        self._stopping = None
        self._idle.clear()

        async def monitor():
            while not task.done():
                if cancellation.cancelled:
                    await asyncio.shield(self._stop(task))
                    return
                await asyncio.sleep(0.02)

        watcher = asyncio.create_task(monitor()) if cancellation is not None else None
        try:
            await asyncio.shield(task)
            if cancellation is not None and cancellation.cancelled:
                raise asyncio.CancelledError()
        finally:
            try:
                # Stop the device before waiting for native inference cleanup.
                stopping = self._stop(task)
                interrupted = await drain(stopping)
                stopping.result()
            finally:
                try:
                    if watcher:
                        await cancel_and_wait(watcher)
                finally:
                    self._task = None
                    self._idle.set()
            if interrupted:
                raise asyncio.CancelledError()

    def _stop(self, task):
        # Every stop path joins one cleanup, so a late stop cannot abort the next response.
        if self._stopping is None:
            self._stopping = asyncio.create_task(self._cleanup(task))
        return self._stopping

    async def _cleanup(self, task):
        if not task.done() and not task.cancelling():
            task.cancel()
        try:
            await self.sink.stop()
        finally:
            await cancel_and_wait(task)

    async def interrupt(self):
        task = self._task
        if task is not None:
            stopping = self._stop(task)
            interrupted = await drain(stopping)
            stopping.result()
            await self._idle.wait()
            if interrupted:
                raise asyncio.CancelledError()

    async def aclose(self):
        self.closed = True
        try:
            await self.interrupt()
        finally:
            await self.service.aclose()

    async def __aenter__(self):
        if self.closed:
            raise TtsClosed()
        return self

    async def __aexit__(self, *_exc):
        await self.aclose()
