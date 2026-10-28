"""Private bounded JSON request / JSON header + PCM response protocol."""

import argparse
import json
import os
import sys
from pathlib import Path

from ._assets import verify_model
from .errors import BackendError


def send(output, value):
    output.write((json.dumps(value) + "\n").encode("ascii"))
    output.flush()


def run(args, output):
    directory = Path(args.model_dir)
    verify_model(directory)
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError:
        raise BackendError("backend_not_installed") from None
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(directory / "model.onnx"),
                lexicon=str(directory / "lexicon.txt"),
                tokens=str(directory / "tokens.txt"),
                dict_dir=str(directory / "dict"),
            ),
            num_threads=args.num_threads,
            debug=False,
            provider="cpu",
        ),
        rule_fsts=",".join(
            str(directory / name) for name in ("phone.fst", "date.fst", "number.fst")
        ),
        max_num_sentences=1,
    )
    if not config.validate():
        raise BackendError("synthesis_failed")
    engine = sherpa_onnx.OfflineTts(config)
    send(output, {"ready": True})
    while line := sys.stdin.buffer.readline(16385):
        if len(line) > 16384 or not line.endswith(b"\n"):
            raise BackendError("synthesis_failed")
        request = json.loads(line)
        text, speed = request["text"], request["speed"]
        if not isinstance(text, str) or not 1 <= len(text) <= 120 or not 0.5 <= speed <= 2:
            raise BackendError("synthesis_failed")
        audio = engine.generate(text, sid=0, speed=speed)
        samples = np.asarray(audio.samples)
        if not 0 < samples.size <= audio.sample_rate * 60 or not np.isfinite(samples).all():
            raise BackendError("synthesis_failed")
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        send(output, {"bytes": len(pcm), "sample_rate": audio.sample_rate})
        output.write(pcm)
        output.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--num-threads", type=int, default=2)
    args = parser.parse_args()
    # Native libraries can write to stdout: keep their output away from the protocol.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0) as output:
        with open(os.devnull, "wb") as quiet:
            os.dup2(quiet.fileno(), sys.stdout.fileno())
        try:
            run(args, output)
        except BackendError as error:
            send(output, {"error": error.code})
        except Exception:
            send(output, {"error": "synthesis_failed"})


if __name__ == "__main__":
    main()
