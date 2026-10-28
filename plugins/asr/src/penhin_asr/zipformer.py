"""CPU Zipformer: a killable worker retains decoder state for one utterance."""

from __future__ import annotations

import asyncio
import json
import struct
import subprocess
import sys
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from .audio import PcmAudio
from .errors import BackendError
from .service import drain

_MAX_RESPONSE = 128 * 1024
_WORKER_ERRORS = {"model_missing", "model_mismatch", "backend_not_installed", "recognition_failed"}


async def _read_response(stream: asyncio.StreamReader) -> dict:
    try:
        line = await stream.readline()
    except ValueError:
        raise BackendError("worker_output_too_large") from None
    if len(line) > _MAX_RESPONSE:
        raise BackendError("worker_output_too_large")
    try:
        response = json.loads(line)
    except (ValueError, UnicodeError):
        raise BackendError("worker_failed") from None
    if not isinstance(response, dict):
        raise BackendError("invalid_worker_result")
    if response.get("error") in _WORKER_ERRORS:
        raise BackendError(response["error"])
    return response


async def _discard(stream: asyncio.StreamReader) -> None:
    while await stream.read(8192):
        pass


async def _reap(process: asyncio.subprocess.Process) -> None:
    assert process.stdout is not None
    await asyncio.gather(process.wait(), _discard(process.stdout))


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(_reap(process), timeout=1)
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await _reap(process)


@dataclass(frozen=True)
class ZipformerBackend:
    model_dir: Path
    num_threads: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_dir", Path(self.model_dir).resolve())
        if type(self.num_threads) is not int or not 1 <= self.num_threads <= 32:
            raise ValueError("num_threads must be within 1..32")

    async def transcribe(self, audio: PcmAudio) -> str:
        async def chunks():
            size = audio.sample_rate // 5 * 2
            for offset in range(0, len(audio.samples), size):
                yield audio.samples[offset : offset + size]

        text = ""
        async for text, _final in self.stream(chunks(), sample_rate=audio.sample_rate):
            pass
        return text

    async def stream(
        self, chunks: AsyncIterable[bytes], *, sample_rate: int
    ) -> AsyncIterator[tuple[str, bool]]:
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "penhin_asr._worker",
                "--model-dir",
                str(self.model_dir),
                "--num-threads",
                str(self.num_threads),
                "--sample-rate",
                str(sample_rate),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=_MAX_RESPONSE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        )
        interrupted = await drain(creation)
        try:
            process = creation.result()
        except OSError:
            if interrupted:
                raise asyncio.CancelledError() from None
            raise BackendError("worker_start_failed") from None
        try:
            if interrupted:
                raise asyncio.CancelledError()
            assert process.stdin is not None and process.stdout is not None
            if await _read_response(process.stdout) != {"ready": True}:
                raise BackendError("worker_failed")
            async for chunk in chunks:
                process.stdin.write(struct.pack("<I", len(chunk)) + chunk)
                await process.stdin.drain()
                response = await _read_response(process.stdout)
                yield self._text(response, final=False), False
            process.stdin.write(struct.pack("<I", 0))
            await process.stdin.drain()
            response = await _read_response(process.stdout)
            text = self._text(response, final=True)
            process.stdin.close()
            await process.wait()
            if process.returncode != 0:
                raise BackendError("worker_failed")
            yield text, True
        except (BrokenPipeError, ConnectionResetError):
            raise BackendError("worker_failed") from None
        finally:
            cleanup = asyncio.create_task(_stop(process))
            interrupted = await drain(cleanup)
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError()

    @staticmethod
    def _text(response: dict, *, final: bool) -> str:
        text = response.get("text")
        if not isinstance(text, str) or len(text) > 16384 or response.get("is_final") is not final:
            raise BackendError("invalid_worker_result")
        return text
