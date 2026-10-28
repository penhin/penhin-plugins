"""Private one-request decoder: media stdin, bounded JSON stdout, no raw logging."""

from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
import warnings
from contextlib import closing

from .errors import MediaError
from .types import IMAGE_TYPES, VIDEO_TYPES, MediaLimits


def _check_pixels(width: float, height: float, limits: MediaLimits) -> None:
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or min(width, height) <= 0
        or width * height > limits.max_pixels
    ):
        raise MediaError("media_limit_exceeded")


def _jpeg(image, limits: MediaLimits) -> str:
    from PIL import Image, ImageOps

    _check_pixels(image.width, image.height, limits)
    with ImageOps.exif_transpose(image) as oriented:
        oriented.thumbnail((limits.max_side, limits.max_side))
        # Flatten transparency on white; preserve black text from transparent screenshots.
        with oriented.convert("RGBA") as rgba, Image.new("RGB", rgba.size, "white") as rgb:
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            stream = io.BytesIO()
            rgb.save(stream, format="JPEG", quality=90)
            data = stream.getvalue()
    if len(data) > limits.max_visual_bytes:
        raise MediaError("media_limit_exceeded")
    return base64.b64encode(data).decode("ascii")


def _image(data: bytes, mime: str, timestamp, limits: MediaLimits) -> list[dict]:
    from PIL import Image

    with Image.open(io.BytesIO(data), formats=[IMAGE_TYPES[mime]]) as image:
        _check_pixels(image.width, image.height, limits)
        if getattr(image, "n_frames", 1) != 1:
            raise MediaError("unsupported_media")
        return [
            {
                "jpeg": _jpeg(image, limits),
                "kind": "image" if timestamp is None else "video_frame",
                "timestamp_seconds": timestamp,
            }
        ]


def _pdf(data: bytes, limits: MediaLimits) -> list[dict]:
    import pypdfium2 as pdfium

    result = []
    with pdfium.PdfDocument(data) as document:
        if not len(document):
            raise MediaError("empty_media")
        if len(document) > min(limits.max_pdf_pages, limits.max_parts):
            raise MediaError("media_limit_exceeded")
        for index in range(len(document)):
            with closing(document[index]) as page:
                width, height = page.get_size()
                if not all(math.isfinite(x) and x > 0 for x in (width, height)):
                    raise MediaError("invalid_media")
                scale = min(2.0, limits.max_side / max(width, height))
                _check_pixels(math.ceil(width * scale), math.ceil(height * scale), limits)
                with closing(page.get_textpage()) as textpage:
                    if textpage.count_chars() > limits.max_text_chars:
                        raise MediaError("media_limit_exceeded")
                    text = textpage.get_text_range()
                    if len(text) > limits.max_text_chars:
                        raise MediaError("media_limit_exceeded")
                with closing(page.render(scale=scale)) as bitmap, bitmap.to_pil() as image:
                    result.append(
                        {
                            "jpeg": _jpeg(image, limits),
                            "kind": "pdf_page",
                            "page": index + 1,
                            "extracted_text": text,
                        }
                    )
    return result


def _video(data: bytes, mime: str, limits: MediaLimits) -> list[dict]:
    import av

    result = []
    # BytesIO prevents path/URL inputs. Restrict demuxers to the declared container.
    with av.open(io.BytesIO(data), format=VIDEO_TYPES[mime], mode="r") as container:
        if len(container.streams.video) != 1:
            raise MediaError("unsupported_media")
        stream = container.streams.video[0]
        _check_pixels(stream.codec_context.width, stream.codec_context.height, limits)
        if (
            container.duration is not None
            and container.duration / av.time_base > limits.max_video_seconds
        ):
            raise MediaError("media_limit_exceeded")
        start = float((stream.start_time or 0) * stream.time_base)
        next_sample = 0.0
        previous = -1.0
        decoded_frames = 0
        for index, frame in enumerate(container.decode(stream)):
            decoded_frames += 1
            if frame.is_corrupt:
                raise MediaError("invalid_media")
            if index >= limits.max_decoded_frames:
                raise MediaError("media_limit_exceeded")
            _check_pixels(frame.width, frame.height, limits)
            if frame.time is None:
                raise MediaError("invalid_media")
            timestamp = max(0.0, float(frame.time) - start)
            if not math.isfinite(timestamp) or timestamp < previous:
                raise MediaError("invalid_media")
            if timestamp > limits.max_video_seconds:
                raise MediaError("media_limit_exceeded")
            previous = timestamp
            if timestamp + 1e-6 < next_sample:
                continue
            if len(result) >= min(limits.max_video_frames, limits.max_parts):
                raise MediaError("media_limit_exceeded")
            with frame.to_image() as image:
                result.append(
                    {
                        "jpeg": _jpeg(image, limits),
                        "kind": "video_frame",
                        "timestamp_seconds": round(timestamp, 6),
                    }
                )
            next_sample = timestamp + limits.frame_interval_seconds
        if not result:
            raise MediaError("empty_media")
        if stream.frames and decoded_frames < stream.frames:
            raise MediaError("invalid_media")
    return result


def decode(config: dict, data: bytes) -> list[dict]:
    limits = MediaLimits(**config["limits"])
    if not data or len(data) > limits.max_attachment_bytes:
        raise MediaError("media_limit_exceeded")
    mime = config["mime_type"]
    if mime in IMAGE_TYPES:
        return _image(data, mime, config.get("timestamp_seconds"), limits)
    if mime == "application/pdf":
        return _pdf(data, limits)
    if mime in VIDEO_TYPES:
        return _video(data, mime, limits)
    raise MediaError("unsupported_media")


def main() -> int:
    # Silence both native and Python decoder diagnostics. Only safe codes escape.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb") as response:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), sys.stdout.fileno())
            os.dup2(null.fileno(), sys.stderr.fileno())
            try:
                config = json.loads(sys.argv[1])
                limits = MediaLimits(**config["limits"])
                data = sys.stdin.buffer.read(limits.max_attachment_bytes + 1)
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    payload = decode(config, data)
                output = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                if len(output) > limits.max_render_bytes:
                    raise MediaError("media_limit_exceeded")
                status = 0
            except ImportError:
                output, status = b'{"error":"decoder_not_installed"}', 1
            except MediaError as error:
                output, status = json.dumps({"error": error.code}).encode(), 1
            except Exception:
                output, status = b'{"error":"invalid_media"}', 1
        response.write(output)
        response.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
