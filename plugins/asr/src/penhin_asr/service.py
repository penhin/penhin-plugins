"""One bounded, cancellable transcription at a time per service."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from typing import Protocol, TypeVar

from .audio import AudioInput, AudioLimits, PcmAudio, Transcript, VoiceSubmission, decode_wav
from .errors import AsrBusy, AsrClosed, AsrError, AsrTimeout, BackendError, InvalidAudio

AttachmentReader = Callable[[str], Awaitable[AudioInput]]
_Result = TypeVar("_Result")


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...


class Backend(Protocol):
    """Cancellation must finish cleanup before this coroutine exits."""

    async def transcribe(self, audio: PcmAudio) -> str: ...

    def stream(
        self, chunks: AsyncIterable[bytes], *, sample_rate: int
    ) -> AsyncIterator[tuple[str, bool]]: ...


async def drain(task: asyncio.Task) -> bool:
    """Wait through repeated caller cancellation; report it after cleanup.

    The caller owns the task's outcome and must retrieve it after draining.
    A child's cancellation is not mistaken for cancellation of the caller.
    """
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


class AsrService:
    """Host-facing API. It never records audio, edits UI, or calls a model.

    Instances bind to one event loop; concurrent calls fail with AsrBusy instead
    of accumulating a queue of recordings. Closing is terminal.
    """

    def __init__(
        self, backend: Backend, *, limits: AudioLimits | None = None, timeout: float = 60.0
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._backend = backend
        self.limits = limits or AudioLimits()
        self.timeout = timeout
        self._closed = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("ASR service belongs to another event loop")
        self._loop = loop
        return loop

    async def transcribe(
        self, audio: AudioInput, *, cancellation: CancellationSignal | None = None
    ) -> Transcript:
        return await self._run(lambda: self._recognize(audio), cancellation)

    async def prepare_voice(
        self,
        source_id: str,
        *,
        read_audio: AttachmentReader,
        cancellation: CancellationSignal | None = None,
    ) -> VoiceSubmission:
        """Prepare user-submitted audio before invoking an Agent.

        read_audio must authorize and bound the read for the host's session.
        Reading and inference share one timeout, busy slot and cancellation scope.
        Failures (including no speech) must stop submission; no model is called here.
        """

        async def prepare():
            if not isinstance(source_id, str) or not 1 <= len(source_id) <= 256:
                raise InvalidAudio("invalid_source_id")
            try:
                audio = await read_audio(source_id)
            except Exception:
                raise BackendError("attachment_unavailable") from None
            if not isinstance(audio, AudioInput) or audio.source_id != source_id:
                raise BackendError("attachment_mismatch")
            return VoiceSubmission.from_transcript(await self._recognize(audio))

        return await self._run(prepare, cancellation)

    async def _recognize(self, audio: AudioInput) -> Transcript:
        pcm = decode_wav(audio, self.limits)
        text = await self._backend.transcribe(pcm)
        if not isinstance(text, str) or len(text) > 16384:
            raise BackendError("invalid_backend_result")
        return Transcript(text.strip(), audio.source_id, pcm.duration_seconds)

    async def _run(
        self, operation: Callable[[], Awaitable[_Result]], cancellation: CancellationSignal | None
    ) -> _Result:
        loop = self._bind()
        if self._closed:
            raise AsrClosed()
        if self._task is not None:
            raise AsrBusy()
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()
        task = asyncio.create_task(operation())
        self._task = task
        deadline = loop.time() + self.timeout
        try:
            while not task.done():
                if cancellation is not None and cancellation.cancelled:
                    raise asyncio.CancelledError()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise AsrTimeout()
                await asyncio.wait({task}, timeout=min(0.02, remaining))
            # A cancelled/closed invocation must never publish a late transcript.
            if self._closed or (cancellation is not None and cancellation.cancelled):
                raise asyncio.CancelledError()
            if loop.time() >= deadline:
                raise AsrTimeout()
            try:
                return task.result()
            except AsrError:
                raise
            except Exception:
                raise BackendError("recognition_failed") from None
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()
            interrupted = await drain(task)
            if not task.cancelled():
                task.exception()
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
            interrupted = await drain(task)
            if not task.cancelled():
                task.exception()
            if interrupted:
                raise asyncio.CancelledError()

    @asynccontextmanager
    async def stream(
        self,
        chunks: AsyncIterable[bytes],
        *,
        sample_rate: int,
        source_id: str,
        cancellation: CancellationSignal | None = None,
    ) -> AsyncIterator[AsyncIterator[Transcript]]:
        """Own one PCM16 stream; exiting the context always drains its worker.

        Snapshots replace the previous text. Only the last is final. Input EOF
        flushes the decoder. The host owns and closes its audio source. At most
        one result is queued; a slow consumer backpressures audio ingestion.
        timeout bounds each next update (including input wait and cold start).
        """
        self._bind()
        if self._closed:
            raise AsrClosed()
        if self._task is not None:
            raise AsrBusy()
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 48000:
            raise InvalidAudio("unsupported_sample_rate")
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 256:
            raise InvalidAudio("invalid_source_id")
        if not isinstance(chunks, AsyncIterable):
            raise InvalidAudio("expected_async_pcm_chunks")
        total = 0
        queue: asyncio.Queue[Transcript] = asyncio.Queue(maxsize=1)

        async def validated():
            nonlocal total
            async for chunk in chunks:
                if not isinstance(chunk, bytes) or not chunk or len(chunk) % 2:
                    raise InvalidAudio("expected_pcm16_chunk")
                if len(chunk) > sample_rate * 2:
                    raise InvalidAudio("chunk_too_large")
                total += len(chunk)
                if total > self.limits.max_bytes:
                    raise InvalidAudio("audio_too_large")
                if total / (2 * sample_rate) > self.limits.max_seconds:
                    raise InvalidAudio("audio_too_long")
                yield chunk
            if total == 0:
                raise InvalidAudio("empty_audio")

        async def produce():
            final = None
            try:
                async with aclosing(validated()) as audio:
                    async with aclosing(
                        self._backend.stream(audio, sample_rate=sample_rate)
                    ) as out:
                        while True:
                            try:
                                text, is_final = await asyncio.wait_for(anext(out), self.timeout)
                            except StopAsyncIteration:
                                break
                            if (
                                final is not None
                                or type(is_final) is not bool
                                or not isinstance(text, str)
                                or len(text) > 16384
                            ):
                                raise BackendError("invalid_backend_result")
                            update = Transcript(
                                text.strip(), source_id, total / (2 * sample_rate), is_final
                            )
                            if is_final:
                                final = update
                            else:
                                await queue.put(update)
                if final is None:
                    raise BackendError("missing_final_result")
                # Final is published only after the backend has reclaimed its worker.
                await queue.put(final)
            except TimeoutError:
                raise AsrTimeout() from None
            except AsrError:
                raise
            except Exception:
                raise BackendError("recognition_failed") from None

        task = asyncio.create_task(produce())
        self._task = task

        async def watch_cancellation():
            while not task.done():
                if cancellation is not None and cancellation.cancelled:
                    task.cancel()
                    return
                await asyncio.sleep(0.02)

        watcher = asyncio.create_task(watch_cancellation()) if cancellation is not None else None

        async def results():
            while True:
                if self._closed or (cancellation is not None and cancellation.cancelled):
                    raise asyncio.CancelledError()
                if task.done():
                    task.result()  # Fail before returning any stale queued text.
                if not queue.empty():
                    result = queue.get_nowait()
                    yield result
                    if result.is_final:
                        return
                elif task.done():
                    return
                else:
                    await asyncio.wait({task}, timeout=0.02)

        try:
            yield results()
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()
            interrupted = await drain(task)
            if not task.cancelled():
                task.exception()
            if watcher is not None:
                watcher.cancel()
                interrupted = await drain(watcher) or interrupted
                if not watcher.cancelled():
                    watcher.exception()
            self._task = None
            if interrupted:
                raise asyncio.CancelledError()

    async def __aenter__(self) -> AsrService:
        self._bind()
        if self._closed:
            raise AsrClosed()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()
