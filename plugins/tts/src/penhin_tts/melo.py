"""MeloTTS runs in one killable CPU process per response, reused across segments."""

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from ._async import drain
from .audio import SpeechAudio
from .errors import BackendError

_ERRORS = {"model_missing", "model_mismatch", "backend_not_installed", "synthesis_failed"}


async def _header(stream):
    try:
        line = await stream.readline()
        value = json.loads(line)
    except (ValueError, UnicodeError):
        raise BackendError("worker_failed") from None
    if not isinstance(value, dict):
        raise BackendError("worker_failed")
    if value.get("error") in _ERRORS:
        raise BackendError(value["error"])
    return value


async def _discard(stream):
    while await stream.read(8192):
        pass


async def _stop(process):
    if process.stdin:
        process.stdin.close()
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    async def reap():
        await asyncio.gather(process.wait(), _discard(process.stdout))

    try:
        await asyncio.wait_for(reap(), 1)
    except TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await reap()


class _Engine:
    def __init__(self, process):
        self.process = process
        self.ready = False

    async def generate(self, text, *, speed):
        process = self.process
        try:
            if not self.ready:
                if await _header(process.stdout) != {"ready": True}:
                    raise BackendError("worker_failed")
                self.ready = True
            request = json.dumps({"text": text, "speed": speed}, ensure_ascii=True)
            process.stdin.write((request + "\n").encode("ascii"))
            await process.stdin.drain()
            header = await _header(process.stdout)
            size, rate = header.get("bytes"), header.get("sample_rate")
            if (
                type(rate) is not int
                or not 8000 <= rate <= 48000
                or type(size) is not int
                or not 0 < size <= rate * 2 * 60
                or size % 2
            ):
                raise BackendError("invalid_worker_audio")
            return SpeechAudio(await process.stdout.readexactly(size), rate)
        except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
            raise BackendError("worker_failed") from None


@dataclass(frozen=True)
class MeloBackend:
    model_dir: Path
    num_threads: int = 2

    def __post_init__(self):
        object.__setattr__(self, "model_dir", Path(self.model_dir).resolve())
        if type(self.num_threads) is not int or not 1 <= self.num_threads <= 32:
            raise ValueError("num_threads must be within 1..32")

    @asynccontextmanager
    async def session(self):
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "penhin_tts._worker",
                "--model-dir",
                str(self.model_dir),
                "--num-threads",
                str(self.num_threads),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4096,
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
            yield _Engine(process)
        finally:
            cleanup = asyncio.create_task(_stop(process))
            interrupted = await drain(cleanup)
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError()
