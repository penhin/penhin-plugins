import io

import pytest

from penhin_media import MediaInput


@pytest.fixture
def image_bytes():
    from PIL import Image

    buffer = io.BytesIO()
    with Image.new("RGB", (64, 32), "red") as image:
        image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def media(image_bytes):
    return MediaInput(image_bytes, "image-1", "image/png")


@pytest.fixture
def pdf_bytes():
    # Tiny native-text PDF with two ordered pages; no external fixture downloads.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for text in ("FIRST PAGE", "SECOND PAGE"):
        stream = f"BT /F1 14 Tf 10 40 Td ({text}) Tj ET".encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    result, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010} 00000 n \n".encode())
    result.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return bytes(result)


@pytest.fixture
def video_bytes():
    import av
    from PIL import Image

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=2)
        stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
        for color in ("red", "red", "green", "green", "blue", "blue"):
            with Image.new("RGB", (64, 32), color) as image:
                frame = av.VideoFrame.from_image(image)
                for packet in stream.encode(frame):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()
