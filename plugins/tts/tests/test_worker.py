import asyncio
import sys

import pytest

from penhin_tts import BackendError, MeloBackend, TtsService, TtsTimeout


def worker(monkeypatch, code, gate=None):
    real_create = asyncio.create_subprocess_exec
    processes = []
    created = asyncio.Event()

    async def create(*args, **kwargs):
        process = await real_create(sys.executable, "-c", code, **kwargs)
        processes.append(process)
        created.set()
        if gate:
            await gate.wait()
        return process

    monkeypatch.setattr("penhin_tts.melo.asyncio.create_subprocess_exec", create)
    return processes, created


async def test_real_transport_reuses_process_per_response(tmp_path, monkeypatch):
    processes, _ = worker(
        monkeypatch,
        """import sys,json
print('{"ready":true}',flush=True)
for line in sys.stdin:
    request=json.loads(line)
    pcm=b'\x01\x00'*100
    print(json.dumps({'bytes':len(pcm),'sample_rate':16000}),flush=True)
    sys.stdout.buffer.write(pcm)
    sys.stdout.buffer.flush()
""".replace("\x01\x00", "\\x01\\x00"),
    )
    async with TtsService(MeloBackend(tmp_path)) as service:
        audio = await service.synthesize("你好。再见。")
    assert len(audio.pcm) == 400 and len(processes) == 1
    assert processes[0].returncode is not None


@pytest.mark.parametrize("during_spawn", [False, True])
async def test_repeated_cancel_reaps_spawned_worker(tmp_path, monkeypatch, during_spawn):
    gate = asyncio.Event() if during_spawn else None
    processes, created = worker(monkeypatch, "import time; time.sleep(60)", gate)
    task = asyncio.create_task(TtsService(MeloBackend(tmp_path)).synthesize("你好"))
    await created.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    if gate:
        gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert processes[0].returncode is not None


@pytest.mark.parametrize(
    "code,error",
    [
        ('print("private malformed output",flush=True)', "worker_failed"),
        ('print(\'{"error":"model_mismatch"}\',flush=True)', "model_mismatch"),
        ('print("x"*100000,flush=True)', "worker_failed"),
        (
            'print(\'{"ready":true}\',flush=True); input(); print(\'{"bytes":999999999,"sample_rate":44100}\',flush=True)',
            "invalid_worker_audio",
        ),
        (
            'print(\'{"ready":true}\',flush=True); input(); print(\'{"bytes":4,"sample_rate":44100}\',flush=True)',
            "worker_failed",
        ),
    ],
)
async def test_bad_protocol_is_bounded_redacted_and_reaped(tmp_path, monkeypatch, code, error):
    processes, _ = worker(monkeypatch, code)
    with pytest.raises(BackendError, match=error):
        await TtsService(MeloBackend(tmp_path)).synthesize("你好")
    assert processes[0].returncode is not None


async def test_model_load_timeout_reaps_worker(tmp_path, monkeypatch):
    processes, _ = worker(monkeypatch, "import time; time.sleep(60)")
    with pytest.raises(TtsTimeout):
        await TtsService(MeloBackend(tmp_path), timeout=0.05).synthesize("你好")
    assert processes[0].returncode is not None


async def test_missing_model_does_not_download(tmp_path):
    with pytest.raises(BackendError, match="model_missing"):
        await TtsService(MeloBackend(tmp_path)).synthesize("你好")
