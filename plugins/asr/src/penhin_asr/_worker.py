"""One streaming utterance: framed PCM16 stdin, bounded JSON-lines stdout."""

import argparse
import json
import os
import struct
import sys
from pathlib import Path

from ._assets import verify_model
from .errors import BackendError


def send(response, payload):
    response.write(json.dumps(payload, ensure_ascii=True) + "\n")
    response.flush()


def read_exact(size):
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise BackendError("recognition_failed")
    return data


def run(args: argparse.Namespace, response) -> None:
    directory = Path(args.model_dir)
    verify_model(directory)
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError:
        raise BackendError("backend_not_installed") from None
    if not 8000 <= args.sample_rate <= 48000:
        raise BackendError("recognition_failed")
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        encoder=str(directory / "encoder.int8.onnx"),
        decoder=str(directory / "decoder.onnx"),
        joiner=str(directory / "joiner.int8.onnx"),
        tokens=str(directory / "tokens.txt"),
        num_threads=args.num_threads,
        enable_endpoint_detection=False,
        decoding_method="greedy_search",
        debug=False,
        provider="cpu",
    )
    stream = recognizer.create_stream()
    send(response, {"ready": True})
    total = 0
    while True:
        size = struct.unpack("<I", read_exact(4))[0]
        if size == 0:
            if total == 0:
                raise BackendError("recognition_failed")
            # Flush right context, as in the upstream streaming-file example.
            stream.accept_waveform(args.sample_rate, np.zeros(args.sample_rate // 2, np.float32))
            stream.input_finished()
        else:
            total += size
            if size % 2 or size > 96000 or total > min(10 * 1024 * 1024, args.sample_rate * 240):
                raise BackendError("recognition_failed")
            samples = np.frombuffer(read_exact(size), dtype="<i2").astype(np.float32) / 32768.0
            stream.accept_waveform(args.sample_rate, samples)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream)
        if not isinstance(text, str) or len(text) > 16384:
            raise BackendError("recognition_failed")
        send(response, {"text": text, "is_final": size == 0})
        if size == 0:
            return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--num-threads", type=int, required=True)
    parser.add_argument("--sample-rate", type=int, required=True)
    args = parser.parse_args()
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as response:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), sys.stdout.fileno())
            os.dup2(null.fileno(), sys.stderr.fileno())
            try:
                run(args, response)
                return 0
            except BackendError as error:
                send(response, {"error": error.code})
            except Exception:
                send(response, {"error": "recognition_failed"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
