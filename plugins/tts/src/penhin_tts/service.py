"""One bounded speech response per service; backend and host remain replaceable."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from typing import Protocol

from ._async import CancellationSignal, cancel_and_wait, watch
from .audio import SpeechAudio, SpeechSegment, TtsLimits
from .errors import BackendError, InvalidText, TtsBusy, TtsClosed, TtsError, TtsTimeout
from .text import segments


class Synthesizer(Protocol):
    async def generate(self, text: str, *, speed: float) -> SpeechAudio: ...


class Backend(Protocol):
    def session(self) -> AbstractAsyncContextManager[Synthesizer]: ...


class TtsService:
    def __init__(self, backend: Backend, *, limits: TtsLimits | None = None, timeout=60.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.backend = backend
        self.limits = limits or TtsLimits()
        self.timeout = timeout
        self._task: asyncio.Task | None = None
        self._loop = None
        self._aborted = None
        self.closed = False

    def _bind(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("TTS service belongs to another event loop")
        self._loop = loop

    async def synthesize(
        self, text: str, *, speed: float = 1.0, cancellation: CancellationSignal | None = None
    ) -> SpeechAudio:
        if not isinstance(text, str):
            raise InvalidText("expected_text")
        parts = []
        rate = 44100
        async with self.stream(text, speed=speed, cancellation=cancellation) as output:
            async for segment in output:
                parts.append(segment.pcm)
                rate = segment.sample_rate
        return SpeechAudio(b"".join(parts), rate)

    @asynccontextmanager
    async def stream(
        self,
        text: str | AsyncIterable[str],
        *,
        response_id: str = "speech",
        speed: float = 1.0,
        cancellation: CancellationSignal | None = None,
    ) -> AsyncIterator[AsyncIterator[SpeechSegment]]:
        self._bind()
        if self.closed:
            raise TtsClosed()
        if self._task is not None:
            raise TtsBusy()
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()
        if not isinstance(response_id, str) or not 1 <= len(response_id) <= 256:
            raise InvalidText("invalid_response_id")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2:
            raise InvalidText("invalid_speed")
        queue: asyncio.Queue[SpeechSegment] = asyncio.Queue(maxsize=1)
        aborted = asyncio.Event()
        self._aborted = aborted

        async def produce():
            try:
                async with aclosing(segments(text, self.limits, self.timeout)) as texts:
                    # Avoid loading the model for empty or punctuation-only answers.
                    first = await anext(texts, None)
                    if first is None:
                        return
                    async with self.backend.session() as engine:
                        index, duration, rate = 0, 0.0, None
                        part = first
                        while part is not None:
                            audio = await asyncio.wait_for(
                                engine.generate(part, speed=speed), self.timeout
                            )
                            if (
                                not isinstance(audio, SpeechAudio)
                                or not isinstance(audio.pcm, bytes)
                                or not audio.pcm
                                or len(audio.pcm) % 2
                                or type(audio.sample_rate) is not int
                                or not 8000 <= audio.sample_rate <= 48000
                                or (rate is not None and rate != audio.sample_rate)
                            ):
                                raise BackendError("invalid_backend_audio")
                            rate = audio.sample_rate
                            duration += audio.duration_seconds
                            if audio.duration_seconds > self.limits.max_segment_seconds:
                                raise BackendError("segment_audio_too_long")
                            if duration > self.limits.max_audio_seconds:
                                raise BackendError("audio_too_long")
                            await queue.put(
                                SpeechSegment(audio.pcm, rate, response_id, index, part)
                            )
                            index += 1
                            part = await anext(texts, None)
            except TimeoutError:
                raise TtsTimeout() from None
            except TtsError:
                raise
            except Exception:
                raise BackendError("synthesis_failed") from None

        task = asyncio.create_task(produce())
        self._task = task
        watcher = (
            asyncio.create_task(watch(task, cancellation)) if cancellation is not None else None
        )

        async def results():
            while True:
                if (
                    aborted.is_set()
                    or self.closed
                    or (cancellation is not None and cancellation.cancelled)
                ):
                    raise asyncio.CancelledError()
                if task.done():
                    task.result()
                if not queue.empty():
                    yield queue.get_nowait()
                elif task.done():
                    return
                else:
                    await asyncio.wait({task}, timeout=0.01)

        try:
            yield results()
        finally:
            try:
                await cancel_and_wait(task)
            finally:
                try:
                    if watcher is not None:
                        await cancel_and_wait(watcher)
                finally:
                    self._task = None
                    self._aborted = None

    async def interrupt(self):
        self._bind()
        if self._aborted is not None:
            self._aborted.set()
        if self._task is not None:
            await cancel_and_wait(self._task)

    async def aclose(self):
        self.closed = True
        await self.interrupt()

    async def __aenter__(self):
        self._bind()
        if self.closed:
            raise TtsClosed()
        return self

    async def __aexit__(self, *_exc):
        await self.aclose()
