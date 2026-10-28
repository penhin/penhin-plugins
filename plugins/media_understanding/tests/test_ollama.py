import base64
import json

import httpx
import pytest

from penhin_media import MediaError, Visual
from penhin_media.ollama import OllamaBackend


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", client)


async def test_local_vision_request_and_model_digest(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "vision:local", "digest": "fixed"}]}
            )
        body = json.loads(request.content)
        assert body["model"] == "vision:local" and body["stream"] is False
        assert body["options"]["num_predict"] == 2048
        assert "secret extracted text" not in body["messages"][0]["content"]
        user = body["messages"][1]
        assert base64.b64decode(user["images"][0]) == b"jpeg"
        assert json.loads(user["content"])["untrusted_extracted_text"] == "secret extracted text"
        return httpx.Response(200, json={"done": True, "message": {"content": "visible content"}})

    install_transport(monkeypatch, handler)
    text = await OllamaBackend("vision:local", expected_digest="fixed").describe(
        Visual(b"jpeg", extracted_text="secret extracted text"),
    )
    assert text == "visible content" and calls == ["/api/tags", "/api/chat"]


@pytest.mark.parametrize(
    "status,payload,code",
    [
        (404, {"error": "secret"}, "model_unavailable"),
        (500, {"error": "secret"}, "backend_failed"),
        (302, {}, "backend_failed"),
        (200, {"done": False, "message": {"content": "partial"}}, "invalid_backend_result"),
        (200, {"done": True, "message": {"content": ""}}, "invalid_backend_result"),
        (
            200,
            {"done": True, "message": {"content": "partial"}, "done_reason": "length"},
            "invalid_backend_result",
        ),
        (200, {"done": True, "message": {"content": "x" * 500}}, "output_limit_exceeded"),
    ],
)
async def test_backend_failures_are_bounded_redacted_and_never_retried(
    monkeypatch, status, payload, code
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=payload)

    install_transport(monkeypatch, handler)
    with pytest.raises(MediaError, match=f"^{code}$"):
        await OllamaBackend("vision", max_response_bytes=256).describe(Visual(b"jpeg"))
    assert len(calls) == 1


async def test_digest_mismatch_prevents_media_transmission(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"models": [{"name": "vision", "digest": "changed"}]})

    install_transport(monkeypatch, handler)
    with pytest.raises(MediaError, match="^model_mismatch$"):
        await OllamaBackend("vision", expected_digest="fixed").describe(Visual(b"jpeg"))
    assert calls == ["/api/tags"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://127.0.0.1.evil.test",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/path",
        "http://127.0.0.1?x=1",
    ],
)
def test_remote_or_credentialed_origin_rejected(url):
    with pytest.raises(ValueError):
        OllamaBackend("vision", base_url=url)


def test_cloud_model_alias_rejected():
    with pytest.raises(ValueError):
        OllamaBackend("vision:cloud")
