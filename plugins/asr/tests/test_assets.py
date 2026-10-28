import hashlib
import io
import tarfile

import pytest

from penhin_asr._assets import verify_model
from penhin_asr.download import provision
from penhin_asr.errors import BackendError


def bundle(tmp_path, monkeypatch, *, symlink=False, bad_file_hash=False):
    archive = tmp_path / "bundle.tar.bz2"
    data = b"fake model"
    with tarfile.open(archive, "w:bz2") as tar:
        model = tarfile.TarInfo("bundle/model.int8.onnx")
        if symlink:
            model.type = tarfile.SYMTYPE
            model.linkname = "../../outside"
            tar.addfile(model)
        else:
            model.size = len(data)
            tar.addfile(model, io.BytesIO(data))
        license_file = tarfile.TarInfo("bundle/LICENSE")
        license_file.size = 7
        tar.addfile(license_file, io.BytesIO(b"license"))
        ignored = tarfile.TarInfo("bundle/../../outside")
        ignored.size = 7
        tar.addfile(ignored, io.BytesIO(b"ignored"))
    lock = {
        "model": "bundle",
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": {
            "model.int8.onnx": "0" * 64 if bad_file_hash else hashlib.sha256(data).hexdigest()
        },
        "samples": {},
        "notices": {"LICENSE": hashlib.sha256(b"license").hexdigest()},
    }
    monkeypatch.setattr("penhin_asr.download.model_lock", lambda: lock)
    return archive


def test_offline_provision_verifies_and_extracts_only_allowlisted_files(tmp_path, monkeypatch):
    archive = bundle(tmp_path, monkeypatch)
    output = tmp_path / "installed"
    provision(output, archive)
    assert (output / "model.int8.onnx").read_bytes() == b"fake model"
    assert (output / "LICENSE").read_text() == "license"
    assert (output / "provenance.json").is_file()
    assert not (tmp_path / "outside").exists()
    with pytest.raises(ValueError, match="already exists"):
        provision(output, archive)
    assert (output / "model.int8.onnx").read_bytes() == b"fake model"


@pytest.mark.parametrize("option", ["symlink", "bad_file_hash", "corrupt_archive"])
def test_rejects_corrupt_assets_without_publishing_directory(tmp_path, monkeypatch, option):
    archive = bundle(
        tmp_path, monkeypatch, symlink=option == "symlink", bad_file_hash=option == "bad_file_hash"
    )
    if option == "corrupt_archive":
        with archive.open("ab") as stream:
            stream.write(b"corrupt")
    output = tmp_path / "installed"
    with pytest.raises(ValueError):
        provision(output, archive)
    assert not output.exists()
    assert not list(tmp_path.glob(".asr-download-*"))


def test_runtime_model_hash_check_rejects_changes(tmp_path, monkeypatch):
    model = tmp_path / "model.int8.onnx"
    model.write_bytes(b"original")
    monkeypatch.setattr(
        "penhin_asr._assets.model_lock",
        lambda: {"files": {model.name: hashlib.sha256(b"original").hexdigest()}},
    )
    verify_model(tmp_path)
    model.write_bytes(b"changed")
    with pytest.raises(BackendError, match="model_mismatch"):
        verify_model(tmp_path)
