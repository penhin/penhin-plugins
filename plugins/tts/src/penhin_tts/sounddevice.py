"""Optional local playback. Each callback supplies at most 512 frames."""

import asyncio

from .errors import BackendError


class SoundDeviceSink:
    def __init__(self, *, device=None):
        self.device = device
        self._stream = None
        self._done = None

    async def write(self, segment):
        try:
            import sounddevice as sd
        except ImportError:
            raise BackendError("playback_not_installed") from None
        if self._stream is not None:
            raise BackendError("playback_busy")
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        self._done = done
        offset = 0

        def finish():
            if not done.done():
                done.set_result(None)

        def callback(outdata, frames, _time, status):
            nonlocal offset
            size = frames * 2
            chunk = segment.pcm[offset : offset + size]
            outdata[:] = chunk + b"\0" * (size - len(chunk))
            offset += len(chunk)
            if offset >= len(segment.pcm):
                raise sd.CallbackStop()

        def finished():
            # PortAudio's C callback returns void; do not return asyncio's Handle.
            loop.call_soon_threadsafe(finish)

        try:
            self._stream = sd.RawOutputStream(
                samplerate=segment.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=512,
                device=self.device,
                callback=callback,
                finished_callback=finished,
            )
            self._stream.start()
            await done
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendError("playback_failed") from None
        finally:
            await self.stop()

    async def stop(self):
        stream, self._stream = self._stream, None
        done, self._done = self._done, None
        try:
            if stream is not None:
                try:
                    stream.abort()
                finally:
                    stream.close()
        finally:
            if done is not None and not done.done():
                done.cancel()
