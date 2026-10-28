import asyncio
import io
import sys

import pytest
from PIL import Image

from penhin_media import MediaError, MediaInput, MediaLimits, MediaService
from penhin_media.renderer import LocalRenderer


async def test_real_png_decode_and_source_frame(media):
    renderer = LocalRenderer()
    for timestamp in (None, 1.25):
        source = MediaInput(media.data, media.source_id, media.mime_type, timestamp)
        visuals = await renderer.render(source, MediaLimits(max_side=16))
        assert len(visuals) == 1
        assert visuals[0].timestamp_seconds == timestamp
        assert visuals[0].kind == ("image" if timestamp is None else "video_frame")
        with Image.open(io.BytesIO(visuals[0].jpeg)) as image:
            assert image.size == (16, 8)


async def test_real_pdf_text_and_page_order(pdf_bytes):
    visuals = await LocalRenderer().render(
        MediaInput(pdf_bytes, "pdf-1", "application/pdf"),
        MediaLimits(),
    )
    assert [v.page for v in visuals] == [1, 2]
    assert [v.extracted_text for v in visuals] == ["FIRST PAGE", "SECOND PAGE"]
    assert all(v.kind == "pdf_page" and v.jpeg.startswith(b"\xff\xd8") for v in visuals)


async def test_real_video_frame_timestamps_and_color_order(video_bytes):
    visuals = await LocalRenderer().render(
        MediaInput(video_bytes, "video-1", "video/mp4"),
        MediaLimits(frame_interval_seconds=1),
    )
    assert [v.timestamp_seconds for v in visuals] == [0, 1, 2]
    colors = []
    for visual in visuals:
        with Image.open(io.BytesIO(visual.jpeg)) as image:
            colors.append(max(range(3), key=lambda i: image.getpixel((16, 16))[i]))
    assert colors == [0, 1, 2]


@pytest.mark.parametrize("mime", ["image/png", "application/pdf", "video/mp4"])
async def test_corrupt_input_returns_only_safe_code(mime, caplog, capsys):
    with pytest.raises(MediaError, match="^invalid_media$"):
        await LocalRenderer().render(MediaInput(b"private data", "id", mime), MediaLimits())
    assert "private data" not in caplog.text + str(capsys.readouterr())


async def test_limits_reject_instead_of_truncating(media, pdf_bytes, video_bytes):
    for source, limits in [
        (media, MediaLimits(max_pixels=100)),
        (MediaInput(pdf_bytes, "pdf", "application/pdf"), MediaLimits(max_pdf_pages=1)),
        (MediaInput(video_bytes, "vid", "video/mp4"), MediaLimits(max_video_seconds=1)),
        (
            MediaInput(video_bytes, "vid", "video/mp4"),
            MediaLimits(max_video_frames=1, frame_interval_seconds=1),
        ),
        (MediaInput(video_bytes, "vid", "video/mp4"), MediaLimits(max_decoded_frames=2)),
    ]:
        with pytest.raises(MediaError, match="^media_limit_exceeded$"):
            await LocalRenderer().render(source, limits)


async def test_declared_mime_is_enforced(image_bytes):
    with pytest.raises(MediaError, match="^invalid_media$"):
        await LocalRenderer().render(MediaInput(image_bytes, "id", "image/jpeg"), MediaLimits())


async def test_worker_timeout_reaps_process(media, monkeypatch):
    import penhin_media.renderer as module

    original = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-c", "import time; time.sleep(300)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)

    class Backend:
        name = "unused"

        async def describe(self, visual):
            pytest.fail("timeout must prevent model calls")

    async def reader(_):
        return media

    async with MediaService(Backend(), timeout=0.2) as service:
        with pytest.raises(MediaError, match="^media_timeout$"):
            await service.understand([media.source_id], read_attachment=reader)
    assert processes and all(p.returncode is not None for p in processes)


async def test_video_full_pipeline_labels_sampling(video_bytes):
    class Backend:
        name = "test"

        async def describe(self, visual):
            return "frame"

    async def reader(source_id):
        return MediaInput(video_bytes, source_id, "video/mp4")

    async with MediaService(Backend(), limits=MediaLimits(frame_interval_seconds=1)) as service:
        result = await service.understand(["video"], read_attachment=reader)
    assert [p.timestamp_seconds for p in result.parts] == [0, 1, 2]
    assert result.warnings == ("video_frames_sampled", "video_audio_not_processed")


async def test_real_webm_decode():
    import av

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="webm") as container:
        stream = container.add_stream("libvpx-vp9", rate=1)
        stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
        with Image.new("RGB", (64, 32), "red") as image:
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    visuals = await LocalRenderer().render(
        MediaInput(buffer.getvalue(), "webm", "video/webm"),
        MediaLimits(),
    )
    assert len(visuals) == 1 and visuals[0].timestamp_seconds == 0


async def test_animated_webp_rejected():
    buffer = io.BytesIO()
    with Image.new("RGB", (32, 32), "red") as first, Image.new("RGB", (32, 32), "blue") as second:
        first.save(buffer, "WEBP", save_all=True, append_images=[second], duration=100, loop=0)
    with pytest.raises(MediaError, match="^unsupported_media$"):
        await LocalRenderer().render(
            MediaInput(buffer.getvalue(), "animated", "image/webp"),
            MediaLimits(),
        )


async def test_cancellation_during_worker_creation_reaps_child(media, monkeypatch):
    import penhin_media.renderer as module

    original = asyncio.create_subprocess_exec
    started, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-c", "import time; time.sleep(300)", **kwargs)
        processes.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(LocalRenderer().render(media, MediaLimits()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None


async def test_excess_worker_output_reaps_child(media, monkeypatch):
    import penhin_media.renderer as module

    original = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        process = await original(sys.executable, "-c", "print('x' * 10000)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(MediaError, match="^media_limit_exceeded$"):
        await LocalRenderer().render(media, MediaLimits(max_render_bytes=100))
    assert processes[0].returncode is not None
