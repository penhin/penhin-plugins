"""Explicit local-file transcription; no microphone access or model submission."""

import argparse
import asyncio
import json
from pathlib import Path

from . import AsrError, AsrService, AudioInput, AudioLimits, ZipformerBackend
from .audio import decode_wav


async def transcribe_file(args: argparse.Namespace) -> dict | None:
    limits = AudioLimits()
    with Path(args.audio).open("rb") as stream:
        data = stream.read(limits.max_bytes + 1)
    async with AsrService(ZipformerBackend(Path(args.model_dir)), timeout=args.timeout) as service:
        audio = AudioInput(data, "cli-audio")
        if args.stream:
            pcm = decode_wav(audio, limits)

            async def chunks():
                size = pcm.sample_rate // 5 * 2
                for offset in range(0, len(pcm.samples), size):
                    yield pcm.samples[offset : offset + size]

            async with service.stream(
                chunks(), sample_rate=pcm.sample_rate, source_id=audio.source_id
            ) as results:
                async for result in results:
                    print(json.dumps(result.as_dict(), ensure_ascii=True), flush=True)
            return None
        result = await service.transcribe(audio)
        return result.as_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a local mono PCM16 WAV offline")
    parser.add_argument("audio")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument(
        "--stream", action="store_true", help="Emit cumulative JSON lines as decoded"
    )
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        result = asyncio.run(transcribe_file(args))
    except AsrError as error:
        result = {"error": error.code}
    except (OSError, ValueError):
        result = {"error": "invalid_configuration_or_file"}
    except KeyboardInterrupt:
        result = {"error": "cancelled"}
    # JSON escapes keep CLI output portable on Windows consoles with legacy encodings.
    if result is not None:
        print(json.dumps(result, ensure_ascii=True))
    return int(result is not None and "error" in result)


if __name__ == "__main__":
    raise SystemExit(main())
