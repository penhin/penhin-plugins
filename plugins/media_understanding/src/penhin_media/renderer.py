"""Bounded native decoding in a disposable process, without workspace files."""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from dataclasses import asdict

from ._async import drain
from .errors import CODES, MediaError
from .types import MediaInput, MediaLimits, Visual


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
    result = bytearray()
    while chunk := await stream.read(8192):
        result.extend(chunk)
        if len(result) > limit:
            raise MediaError("media_limit_exceeded")
    return bytes(result)


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
    else:
        await process.wait()


class LocalRenderer:
    """Each call owns its worker. Use through MediaService for timeout/cancellation.

    Process separation enables normal cancellation of native decoders; it is not
    an OS sandbox or a guarantee against malicious plugin dependencies.
    """

    async def render(self, media: MediaInput, limits: MediaLimits) -> tuple[Visual, ...]:
        media.validate(limits)
        config = json.dumps(
            {
                "mime_type": media.mime_type,
                "timestamp_seconds": media.timestamp_seconds,
                "limits": asdict(limits),
            }
        )
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "penhin_media._worker",
                config,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        )
        interrupted = await drain(creation)
        try:
            process = creation.result()
        except OSError:
            if interrupted:
                raise asyncio.CancelledError() from None
            raise MediaError("worker_start_failed") from None
        reader = None
        try:
            if interrupted:
                raise asyncio.CancelledError()
            assert process.stdin is not None and process.stdout is not None
            reader = asyncio.create_task(_read_bounded(process.stdout, limits.max_render_bytes))
            try:
                process.stdin.write(media.data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
            output = await asyncio.shield(reader)
            await process.wait()
            try:
                payload = json.loads(output)
                if isinstance(payload, dict) and payload.get("error") in CODES:
                    raise MediaError(payload["error"])
                if process.returncode or not isinstance(payload, list) or not payload:
                    raise MediaError("worker_failed")
                if len(payload) > limits.max_parts:
                    raise MediaError("media_limit_exceeded")
                return tuple(
                    Visual(
                        jpeg=base64.b64decode(item["jpeg"], validate=True),
                        kind=item["kind"],
                        page=item.get("page"),
                        timestamp_seconds=item.get("timestamp_seconds"),
                        extracted_text=item.get("extracted_text", ""),
                    )
                    for item in payload
                )
            except MediaError:
                raise
            except Exception:
                raise MediaError("worker_failed") from None
        finally:
            cleanup = asyncio.create_task(_stop(process))
            interrupted = await drain(cleanup)
            cleanup.result()
            if reader is not None:
                if not reader.done():
                    reader.cancel()
                interrupted = await drain(reader) or interrupted
                if not reader.cancelled():
                    reader.exception()
            if interrupted:
                raise asyncio.CancelledError()
