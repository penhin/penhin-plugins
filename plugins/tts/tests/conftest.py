import asyncio
from contextlib import asynccontextmanager

import pytest

from penhin_tts import SpeechAudio


class FakeBackend:
    def __init__(self):
        self.texts = []
        self.sessions = 0
        self.active = False
        self.entered = asyncio.Event()
        self.gate = None
        self.cleanup_gate = None
        self.cleaning = asyncio.Event()
        self.result = SpeechAudio(b"\x01\x00" * 800, 16000)

    @asynccontextmanager
    async def session(self):
        self.sessions += 1
        self.active = True
        try:
            yield self
        finally:
            self.cleaning.set()
            if self.cleanup_gate:
                await self.cleanup_gate.wait()
            self.active = False

    async def generate(self, text, *, speed):
        self.texts.append((text, speed))
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        return self.result


class FakeSink:
    def __init__(self):
        self.writes = []
        self.stops = 0
        self.entered = asyncio.Event()
        self.gate = None

    async def write(self, segment):
        self.writes.append(segment)
        self.entered.set()
        if self.gate:
            await self.gate.wait()

    async def stop(self):
        self.stops += 1


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def sink():
    return FakeSink()
