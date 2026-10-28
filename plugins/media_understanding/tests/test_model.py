"""Opt-in real model smoke test; no model downloads or server startup in tests."""

import os

import pytest

from penhin_media import MediaService
from penhin_media.ollama import OllamaBackend

pytestmark = pytest.mark.model


@pytest.mark.skipif(
    not os.environ.get("PENHIN_MEDIA_MODEL"), reason="Set an installed local vision model"
)
async def test_local_model_recognizes_red_image(media):
    async def reader(_):
        return media

    backend = OllamaBackend(
        os.environ["PENHIN_MEDIA_MODEL"],
        base_url=os.environ.get("PENHIN_MEDIA_OLLAMA_URL", "http://127.0.0.1:11434"),
        expected_digest=os.environ.get("PENHIN_MEDIA_MODEL_DIGEST"),
    )
    async with MediaService(backend) as service:
        result = await service.understand([media.source_id], read_attachment=reader)
    assert "red" in result.parts[0].text.lower() or "红" in result.parts[0].text
