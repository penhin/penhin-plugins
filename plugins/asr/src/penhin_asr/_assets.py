"""Pinned, explicitly provisioned model assets; inference never downloads."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from .errors import BackendError


def model_lock() -> dict:
    return json.loads(files("penhin_asr").joinpath("model-lock.json").read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_model(directory: Path) -> None:
    for name, digest in model_lock()["files"].items():
        path = directory / name
        try:
            if not path.is_file():
                raise BackendError("model_missing")
            if sha256(path) != digest:
                raise BackendError("model_mismatch")
        except OSError:
            raise BackendError("model_missing") from None
