"""Explicit file synthesis or local playback; no model request or microphone."""

import argparse
import asyncio
import codecs
import os
import queue
import sys
import threading
import wave
from pathlib import Path

from . import MeloBackend, SoundDeviceSink, SpeechPlayer, TtsError, TtsService


async def stdin_deltas():
    """Bound a pipe reader without leaving a blocked executor on Ctrl+C.

    A daemon uses raw os.read so interpreter shutdown never waits on stdin's
    buffered IO lock. Ownership is CLI-only; this reader exits with the process.
    """
    values = queue.Queue(maxsize=1)
    stopped = threading.Event()
    fd = sys.stdin.fileno()

    def put(value):
        while not stopped.is_set():
            try:
                values.put(value, timeout=0.05)
                return
            except queue.Full:
                pass

    def read():
        decoder = codecs.getincrementaldecoder(sys.stdin.encoding or "utf-8")()
        try:
            while not stopped.is_set():
                data = os.read(fd, 4096)
                if not data:
                    put(decoder.decode(b"", final=True))
                    break
                put(decoder.decode(data))
        except (OSError, UnicodeError):
            put(TtsError("stdin_read_failed"))
        finally:
            put(None)

    threading.Thread(target=read, daemon=True).start()
    try:
        while True:
            try:
                value = values.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            if value is None:
                return
            if isinstance(value, Exception):
                raise value
            yield value
    finally:
        stopped.set()


async def run(args):
    if args.stream:
        text = stdin_deltas()
    else:
        text = args.text
    service = TtsService(MeloBackend(args.model_dir, args.threads))
    async with service:
        if args.play:
            async with SpeechPlayer(service, SoundDeviceSink()) as player:
                await player.speak(text, speed=args.speed)
        else:
            # Exclusive creation prevents accidental overwrite of a user file.
            with args.output.open("xb") as target:
                try:
                    with wave.open(target, "wb") as output:
                        output.setnchannels(1)
                        output.setsampwidth(2)
                        output.setframerate(44100)
                        async with service.stream(text, speed=args.speed) as segments:
                            first = True
                            async for segment in segments:
                                if first:
                                    output.setframerate(segment.sample_rate)
                                    first = False
                                output.writeframes(segment.pcm)
                except BaseException:
                    target.close()
                    args.output.unlink(missing_ok=True)
                    raise


def main():
    parser = argparse.ArgumentParser(description="Offline Chinese TTS with MeloTTS")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Complete plain text")
    source.add_argument(
        "--stream", action="store_true", help="Read incremental text lines from stdin"
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path, help="New WAV file")
    destination.add_argument("--play", action="store_true", help="Play locally; Ctrl+C interrupts")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (TtsError, OSError, ValueError) as error:
        print(error.code if isinstance(error, TtsError) else "tts_cli_failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
