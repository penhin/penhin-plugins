"""Bound incremental text without waiting for the entire answer."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

from ._async import cancel_and_wait
from .audio import TtsLimits
from .errors import InvalidText, TtsTimeout


def _boundary(text: str, limit: int) -> int:
    for i, char in enumerate(text[:limit]):
        if char in "。！？!?；;\n" or (char in "，,:：" and i >= 11):
            return i + 1
        # Keep decimal points and English words intact where lookahead is available.
        if char == "." and i + 1 < len(text) and not text[i + 1].isdigit():
            return i + 1
    if len(text) >= limit:
        space = text.rfind(" ", limit // 2, limit)
        return space + 1 if space >= 0 else limit
    return 0


async def segments(
    source: str | AsyncIterable[str], limits: TtsLimits, timeout: float
) -> AsyncIterator[str]:
    if isinstance(source, str):
        if len(source) > limits.max_text_chars:
            raise InvalidText("text_too_large")
        complete = source

        async def once():
            yield complete

        source = once()
    if not isinstance(source, AsyncIterable):
        raise InvalidText("expected_text_or_async_deltas")
    iterator = aiter(source)
    pending = None
    buffer = ""
    total = 0
    loop = asyncio.get_running_loop()
    buffered_at = loop.time()
    idle_since = loop.time()
    try:
        while True:
            boundary = _boundary(buffer, limits.max_segment_chars)
            if boundary:
                text, buffer = buffer[:boundary], buffer[boundary:]
                if any(c.isalnum() for c in text):
                    yield text.strip()
                buffered_at = loop.time()
                continue
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
                idle_since = loop.time()
            wait = timeout - (loop.time() - idle_since)
            if buffer:
                wait = min(wait, limits.flush_seconds - (loop.time() - buffered_at))
            done, _ = await asyncio.wait({pending}, timeout=max(0, wait))
            if not done:
                if loop.time() - idle_since >= timeout:
                    raise TtsTimeout()
                text, buffer = buffer, ""
                if any(c.isalnum() for c in text):
                    yield text.strip()
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                if any(c.isalnum() for c in buffer):
                    yield buffer.strip()
                return
            finally:
                pending = None
            if not isinstance(chunk, str):
                raise InvalidText("expected_text_delta")
            total += len(chunk)
            if total > limits.max_text_chars:
                raise InvalidText("text_too_large")
            if not buffer:
                buffered_at = loop.time()
            buffer += chunk
    finally:
        if pending is not None:
            await cancel_and_wait(pending)
        # The host owns the source, including closing its network connection.
