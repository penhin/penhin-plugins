import asyncio
import sys

import pytest

from penhin_asr import AsrService, BackendError, ZipformerBackend


def stub_worker(monkeypatch, code, *, gate=None):
    real_create = asyncio.create_subprocess_exec
    processes = []
    created = asyncio.Event()

    async def create(*args, **kwargs):
        process = await real_create(sys.executable, "-c", code, **kwargs)
        processes.append(process)
        created.set()
        if gate is not None:
            await gate.wait()
        return process

    monkeypatch.setattr("penhin_asr.zipformer.asyncio.create_subprocess_exec", create)
    return processes, created


async def test_real_subprocess_transport(audio, tmp_path, monkeypatch):
    processes, _ = stub_worker(
        monkeypatch,
        """import sys,json,struct
print(json.dumps({"ready":True}),flush=True)
total=0
while True:
    size=struct.unpack('<I',sys.stdin.buffer.read(4))[0]
    total+=len(sys.stdin.buffer.read(size))
    print(json.dumps({"text":str(total),"is_final":size==0}),flush=True)
    if size==0: break
""",
    )
    async with AsrService(ZipformerBackend(tmp_path)) as service:
        assert (await service.transcribe(audio)).text == "1600"
    assert processes[0].returncode == 0


@pytest.mark.parametrize("during_spawn", [False, True])
async def test_cancel_reaps_worker_including_during_creation(
    audio, tmp_path, monkeypatch, during_spawn
):
    gate = asyncio.Event() if during_spawn else None
    processes, created = stub_worker(monkeypatch, "import time; time.sleep(60)", gate=gate)
    async with AsrService(ZipformerBackend(tmp_path)) as service:
        task = asyncio.create_task(service.transcribe(audio))
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        if gate is not None:
            gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert processes[0].returncode is not None


@pytest.mark.parametrize(
    "output,code",
    [
        ('print("private invalid response")', "worker_failed"),
        ('print(\'{"error":"model_mismatch"}\')', "model_mismatch"),
        ("print('{\"text\":42}')", "worker_failed"),
        ('print("X" * 2000000)', "worker_output_too_large"),
    ],
)
async def test_worker_protocol_errors_are_bounded_and_redacted(
    audio, tmp_path, monkeypatch, output, code
):
    processes, _ = stub_worker(monkeypatch, output)
    async with AsrService(ZipformerBackend(tmp_path)) as service:
        with pytest.raises(BackendError, match=code):
            await service.transcribe(audio)
    assert processes[0].returncode is not None


async def test_missing_model_fails_without_download(audio, tmp_path):
    async with AsrService(ZipformerBackend(tmp_path)) as service:
        with pytest.raises(BackendError, match="model_missing"):
            await service.transcribe(audio)


@pytest.mark.parametrize("stop", ["break", "close", "timeout"])
async def test_stream_context_reaps_real_worker_after_partial(tmp_path, monkeypatch, stop):
    processes, _ = stub_worker(
        monkeypatch,
        """import sys,json,struct,time
print(json.dumps({"ready": True}),flush=True)
size=struct.unpack('<I',sys.stdin.buffer.read(4))[0]
sys.stdin.buffer.read(size)
print(json.dumps({"text":"partial","is_final":False}),flush=True)
time.sleep(60)
""",
    )

    async def audio():
        yield b"\0\0" * 1600
        yield b"\0\0" * 1600

    from penhin_asr import AsrTimeout

    async with AsrService(ZipformerBackend(tmp_path), timeout=0.5) as service:
        async with service.stream(audio(), sample_rate=16000, source_id="mic") as results:
            assert (await anext(results)).text == "partial"
            if stop == "close":
                await service.aclose()
                with pytest.raises(asyncio.CancelledError):
                    await anext(results)
            elif stop == "timeout":
                with pytest.raises(AsrTimeout):
                    await anext(results)
        assert processes[0].returncode is not None
