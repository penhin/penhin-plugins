import asyncio
import json

import pytest

from penhin_media import MediaError, MediaInput, MediaLimits, MediaService, Visual


class Renderer:
    async def render(self, media, limits):
        return (Visual(b"jpeg"),)


class Backend:
    name = "test"

    async def describe(self, visual):
        return "untrusted description"


class Blocking:
    name = "blocking"

    def __init__(self):
        self.entered, self.cleaning, self.release = (asyncio.Event() for _ in range(3))
        self.finished = False

    async def describe(self, visual):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release.wait()
            self.finished = True


async def test_order_provenance_and_no_raw_content_in_repr(media):
    async def reader(source_id):
        return MediaInput(media.data, source_id, media.mime_type)

    async with MediaService(Backend(), renderer=Renderer()) as service:
        result = await service.understand(["b", "a", "b"], read_attachment=reader)
    assert [(p.source_id, p.attachment_index, p.part_index) for p in result.parts] == [
        ("b", 0, 0),
        ("a", 1, 0),
        ("b", 2, 0),
    ]
    data = result.as_dict()
    assert data["trust"] == "untrusted" and data["plugin"] == "media_understanding"
    assert json.loads(result.as_text_block()["text"]) == data
    assert "untrusted description" not in repr(result)
    assert "untrusted description" not in repr(result.parts[0])
    assert repr(media.data) not in repr(media)


async def test_enablement_before_reads_and_first_use_only(media):
    calls = []
    enabled = False

    async def enable():
        nonlocal enabled
        if not enabled:
            calls.append("prompt")
            enabled = True
        return enabled

    async def reader(_):
        calls.append("read")
        return media

    async with MediaService(Backend(), renderer=Renderer()) as service:
        for _ in range(2):
            await service.prepare_fallback(
                [media.source_id], read_attachment=reader, ensure_enabled=enable
            )
    assert calls == ["prompt", "read", "read"]


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
async def test_denied_project_never_reads_or_starts_backend(answer):
    async def enable():
        return answer

    async def reader(_):
        pytest.fail("must not read disabled project media")

    async with MediaService(Backend(), renderer=Renderer()) as service:
        with pytest.raises(MediaError, match="^project_not_enabled$"):
            await service.prepare_fallback(["id"], read_attachment=reader, ensure_enabled=enable)


async def test_failure_discards_whole_batch_and_redacts_reader_error(media):
    calls = []

    async def reader(source_id):
        calls.append(source_id)
        if source_id == "private":
            raise PermissionError("secret path and attachment data")
        return media

    async with MediaService(Backend(), renderer=Renderer()) as service:
        with pytest.raises(MediaError, match="^attachment_unavailable$"):
            await service.understand([media.source_id, "private", "later"], read_attachment=reader)
    assert calls == [media.source_id, "private"]


async def test_mismatched_identity_never_decodes(media):
    async def reader(_):
        return media

    async with MediaService(Backend(), renderer=Renderer()) as service:
        with pytest.raises(MediaError, match="^attachment_mismatch$"):
            await service.understand(["other"], read_attachment=reader)


@pytest.mark.parametrize(
    "text,code",
    [
        ("", "invalid_backend_result"),
        (None, "invalid_backend_result"),
        ("x" * 21, "output_limit_exceeded"),
    ],
)
async def test_bad_backend_results_fail_submission(media, text, code):
    class Bad(Backend):
        async def describe(self, visual):
            return text

    async def reader(_):
        return media

    async with MediaService(
        Bad(), renderer=Renderer(), limits=MediaLimits(max_text_chars=20)
    ) as service:
        with pytest.raises(MediaError, match=f"^{code}$"):
            await service.understand([media.source_id], read_attachment=reader)


async def test_batch_byte_and_output_limits(media):
    async def reader(_):
        return media

    for limits, code in [
        (MediaLimits(max_total_bytes=len(media.data)), "media_limit_exceeded"),
        (MediaLimits(max_output_chars=25), "output_limit_exceeded"),
        (MediaLimits(max_parts=1), "media_limit_exceeded"),
    ]:
        async with MediaService(Backend(), renderer=Renderer(), limits=limits) as service:
            with pytest.raises(MediaError, match=f"^{code}$"):
                await service.understand([media.source_id] * 2, read_attachment=reader)


async def test_repeated_cancel_waits_for_cleanup_and_busy_until_done(media):
    backend = Blocking()

    async def reader(_):
        return media

    async with MediaService(backend, renderer=Renderer()) as service:
        task = asyncio.create_task(service.understand([media.source_id], read_attachment=reader))
        await backend.entered.wait()
        task.cancel()
        await backend.cleaning.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(MediaError, match="^media_busy$"):
            await service.understand([media.source_id], read_attachment=reader)
        backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.finished


async def test_timeout_includes_read_and_waits_for_cleanup():
    cleaned = asyncio.Event()

    async def reader(_):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async with MediaService(Backend(), renderer=Renderer(), timeout=0.03) as service:
        with pytest.raises(MediaError, match="^media_timeout$"):
            await service.understand(["id"], read_attachment=reader)
        assert cleaned.is_set()


async def test_token_and_close_stop_direct_processing(media):
    class Token:
        cancelled = False

    async def reader(_):
        return media

    for by_token in (True, False):
        backend, token = Blocking(), Token()
        service = MediaService(backend, renderer=Renderer())
        task = asyncio.create_task(
            service.understand(
                [media.source_id],
                read_attachment=reader,
                cancellation=token,
            )
        )
        await backend.entered.wait()
        if by_token:
            token.cancelled = True
        else:
            closing = asyncio.create_task(service.aclose())
        await backend.cleaning.wait()
        assert not task.done()
        backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        if not by_token:
            await closing
        await service.aclose()
        with pytest.raises(MediaError, match="^media_closed$"):
            await service.understand([media.source_id], read_attachment=reader)


async def test_precancel_does_not_read():
    class Token:
        cancelled = True

    async def reader(_):
        pytest.fail("pre-cancelled read")

    async with MediaService(Backend(), renderer=Renderer()) as service:
        with pytest.raises(asyncio.CancelledError):
            await service.understand(["id"], read_attachment=reader, cancellation=Token())
