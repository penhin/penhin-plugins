import asyncio
import sys
from types import SimpleNamespace

import pytest

from penhin_tts import BackendError, SoundDeviceSink, SpeechSegment


async def test_device_callbacks_drain_pcm_and_abort_on_stop(monkeypatch):
    streams = []

    class CallbackStop(Exception):
        pass

    class Stream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.aborted = self.closed = False
            streams.append(self)

        def start(self):
            pass

        def abort(self):
            self.aborted = True

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(RawOutputStream=Stream, CallbackStop=CallbackStop),
    )
    sink = SoundDeviceSink()
    segment = SpeechSegment(b"\x01\x00" * 600, 44100, "answer", 0, "你好")
    task = asyncio.create_task(sink.write(segment))
    await asyncio.sleep(0)
    stream = streams[0]
    first, last = bytearray(1024), bytearray(1024)
    stream.kwargs["callback"](first, 512, None, None)
    assert first == segment.pcm[:1024]
    with pytest.raises(CallbackStop):
        stream.kwargs["callback"](last, 512, None, None)
    assert last[:176] == segment.pcm[1024:] and not any(last[176:])
    assert not task.done()
    assert stream.kwargs["finished_callback"]() is None
    await task
    assert stream.aborted and stream.closed

    task = asyncio.create_task(sink.write(segment))
    await asyncio.sleep(0)
    await sink.stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert streams[1].aborted and streams[1].closed


async def test_device_failure_is_redacted(monkeypatch):
    def fail(**kwargs):
        raise OSError("private device name")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawOutputStream=fail))
    with pytest.raises(BackendError, match="playback_failed"):
        await SoundDeviceSink().write(SpeechSegment(b"00", 44100, "r", 0, "你好"))
