"""Opt-in voice turns: final ASR -> host answer deltas -> interruptible TTS."""

import asyncio
from contextlib import aclosing

from ._async import cancel_and_wait, drain
from .errors import TtsBusy, TtsClosed


class VoiceInteraction:
    """The host calls run_turn only after user submission.

    respond(text) returns an async iterator of *append-only spoken text deltas*.
    This controller owns that iterator and closes it on interruption. The host
    must cancel any separate Agent task in the iterator's finally block.
    ASR is borrowed; player (and its service) is owned. No microphone or VAD.
    """

    def __init__(self, asr, player, respond):
        self.asr, self.player, self.respond = asr, player, respond
        self._task = None
        self._stopping = None
        self._idle = asyncio.Event()
        self._idle.set()
        self.closed = False

    async def run_turn(self, audio, *, response_id="voice", cancellation=None):
        if self.closed:
            raise TtsClosed()
        if self._task is not None:
            raise TtsBusy()
        if cancellation is not None and cancellation.cancelled:
            raise asyncio.CancelledError()

        async def turn():
            transcript = await self.asr.transcribe(audio, cancellation=cancellation)
            if not transcript.text.strip():
                return transcript
            async with aclosing(self.respond(transcript.text)) as deltas:
                await self.player.speak(deltas, response_id=response_id, cancellation=cancellation)
            return transcript

        task = asyncio.create_task(turn())
        self._task = task
        self._stopping = None
        self._idle.clear()
        try:
            return await asyncio.shield(task)
        finally:
            try:
                cleanup = self._stop(task)
                interrupted = await drain(cleanup)
                cleanup.result()
                if interrupted:
                    raise asyncio.CancelledError()
            finally:
                self._task = None
                self._idle.set()

    def _stop(self, task):
        if self._stopping is None:
            self._stopping = asyncio.create_task(self._cleanup(task))
        return self._stopping

    async def _cleanup(self, task):
        if not task.done() and not task.cancelling():
            task.cancel()
        try:
            await self.player.interrupt()
        finally:
            await cancel_and_wait(task)

    async def interrupt(self):
        task = self._task
        if task is not None:
            cleanup = self._stop(task)
            interrupted = await drain(cleanup)
            cleanup.result()
            await self._idle.wait()
            if interrupted:
                raise asyncio.CancelledError()

    async def aclose(self):
        self.closed = True
        try:
            await self.interrupt()
        finally:
            await self.player.aclose()
