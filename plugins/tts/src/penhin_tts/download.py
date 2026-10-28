"""Explicit model provisioning with pinned hashes and allowlisted extraction."""

import argparse
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from ._assets import model_lock, sha256


def provision(output: Path, archive: Path | None = None) -> None:
    lock = model_lock()
    output = output.resolve()
    if output.exists():
        raise ValueError("output already exists; choose a new model directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tts-download-", dir=output.parent) as temporary:
        stage = Path(temporary)
        if archive is None:
            archive = stage / "model.tar.bz2"
            with urllib.request.urlopen(lock["archive_url"], timeout=30) as response:
                with archive.open("wb") as target:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > lock["archive_bytes"]:
                            raise ValueError("model archive exceeds pinned size")
                        target.write(chunk)
        if (
            archive.stat().st_size != lock["archive_bytes"]
            or sha256(archive) != lock["archive_sha256"]
        ):
            raise ValueError("model archive digest mismatch")
        extracted = stage / "model"
        extracted.mkdir()
        expected = {**lock["files"], **lock["samples"], **lock["notices"]}
        names = set(expected)
        found = set()
        with tarfile.open(archive, "r:bz2") as source:
            for member in source:
                prefix = lock["model"] + "/"
                name = member.name.removeprefix(prefix)
                if not member.name.startswith(prefix) or name not in names:
                    continue
                if name in found or not member.isfile() or member.size > 300 * 1024 * 1024:
                    raise ValueError("unexpected model archive entry")
                target = extracted / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content, target.open("wb") as destination:
                    shutil.copyfileobj(content, destination, length=1024 * 1024)
                if name in expected and sha256(target) != expected[name]:
                    raise ValueError("extracted asset digest mismatch")
                found.add(name)
        if found != names:
            raise ValueError("model archive is missing required assets")
        (extracted / "provenance.json").write_text(
            json.dumps(lock, indent=2) + "\n", encoding="utf-8"
        )
        if output.exists():
            raise ValueError("output already exists; choose a new model directory")
        extracted.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify the pinned MeloTTS model")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="Use an already downloaded archive offline")
    args = parser.parse_args()
    provision(args.output, args.archive)
    print("MeloTTS assets verified and installed.")


if __name__ == "__main__":
    main()
