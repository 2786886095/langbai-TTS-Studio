from __future__ import annotations

import hashlib
import urllib.request
import zipfile

from installer_fixtures import create_tiny_source_archive, local_http_fixture, write_tiny_model


def test_tiny_source_archive_fixture_is_local_and_hash_pinned(tmp_path) -> None:
    artifact = create_tiny_source_archive(tmp_path / "source-commit.zip")
    assert artifact["size"] > 0
    assert len(artifact["sha256"]) == 64
    assert hashlib.sha256(artifact["path"].read_bytes()).hexdigest() == artifact["sha256"]
    with zipfile.ZipFile(artifact["path"]) as archive:
        assert sorted(archive.namelist()) == ["fixture-commit/LICENSE", "fixture-commit/README.md"]


def test_tiny_http_model_fixture_has_stable_size_and_checksum(tmp_path) -> None:
    artifact = write_tiny_model(tmp_path / "http" / "model.bin", size=8192)
    with local_http_fixture(tmp_path / "http") as base_url:
        with urllib.request.urlopen(f"{base_url}/model.bin", timeout=5) as response:
            payload = response.read()
    assert len(payload) == artifact["size"]
    assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
