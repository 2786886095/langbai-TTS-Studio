import hashlib
import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from app.installer.runner import CommandStopped
from app.installer.supply import ManagedSupply


class MemoryResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def zip_payload(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_verified_download_retries_network_errors_and_removes_partial(tmp_path):
    payload = b"pinned archive"
    attempts = []

    def open_fixture(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("fixture outage")
        return MemoryResponse(payload)

    supply = ManagedSupply(urlopen=open_fixture, retry_delay=lambda _: None)
    destination = tmp_path / "archive.zip"
    supply.download_verified(
        "https://official.example/archive.zip", destination, hashlib.sha256(payload).hexdigest(),
        on_progress=lambda *_: None, stop_reason=lambda: None,
    )
    assert destination.read_bytes() == payload
    assert len(attempts) == 3
    assert not (tmp_path / "archive.zip.part").exists()


def test_hash_mismatch_and_cancel_never_publish_partial_download(tmp_path):
    payload = b"tampered"
    supply = ManagedSupply(urlopen=lambda *_args, **_kwargs: MemoryResponse(payload))
    destination = tmp_path / "archive.zip"
    with pytest.raises(RuntimeError, match="SHA-256 校验失败"):
        supply.download_verified(
            "https://official.example/archive.zip", destination, "0" * 64,
            on_progress=lambda *_: None, stop_reason=lambda: None,
        )
    assert not destination.exists()
    assert not (tmp_path / "archive.zip.part").exists()

    with pytest.raises(CommandStopped):
        supply.download_verified(
            "https://official.example/archive.zip", destination, hashlib.sha256(payload).hexdigest(),
            on_progress=lambda *_: None, stop_reason=lambda: "cancel",
        )
    assert not destination.exists()


def test_zip_traversal_is_rejected_and_destination_cleaned(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    archive_path.write_bytes(zip_payload({"../outside.txt": b"no"}))
    destination = tmp_path / "extract"
    with pytest.raises(RuntimeError, match="越界"):
        ManagedSupply().extract_zip(archive_path, destination, stop_reason=lambda: None)
    assert not destination.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_managed_tool_install_detects_damage_and_repairs_atomically(tmp_path):
    payload = zip_payload({"fixture/bin/tool.exe": b"verified executable"})
    item = {
        "id": "fixture", "name": "Fixture", "version": "1.2.3", "platform": "windows-x64",
        "archive_url": "https://official.example/fixture-1.2.3.zip",
        "sha256": hashlib.sha256(payload).hexdigest(), "archive_bytes": len(payload),
        "executables": ["bin/tool.exe"], "license": "Fixture License",
        "license_url": "https://official.example/license", "source_page": "https://official.example/release",
    }
    supply = ManagedSupply(
        {"fixture": item}, urlopen=lambda *_args, **_kwargs: MemoryResponse(payload),
        retry_delay=lambda _: None,
    )
    root = tmp_path / "managed"
    executable = supply.ensure_tool(
        root, "fixture", license_accepted_at="2026-07-19T00:00:00Z", job_id="first",
        on_progress=lambda *_: None, stop_reason=lambda: None,
    )
    assert executable.read_bytes() == b"verified executable"
    assert supply.tool_status(root, "fixture")["installed"]
    manifest = json.loads((executable.parents[1] / "tool-manifest.json").read_text(encoding="utf-8"))
    assert manifest["archiveSha256"] == item["sha256"]
    assert manifest["licenseAcceptedAt"] == "2026-07-19T00:00:00Z"

    executable.write_bytes(b"damaged")
    assert supply.tool_status(root, "fixture")["state"] == "integrity_failed"
    repaired = supply.ensure_tool(
        root, "fixture", license_accepted_at="2026-07-19T01:00:00Z", job_id="repair",
        on_progress=lambda *_: None, stop_reason=lambda: None,
    )
    assert repaired.read_bytes() == b"verified executable"
    assert supply.tool_status(root, "fixture")["installed"]
    assert not (root / ".installer-tmp" / "repair" / "tools" / "fixture").exists()


def test_failed_post_swap_verification_restores_previous_tool(tmp_path, monkeypatch):
    payload = zip_payload({"fixture/tool.exe": b"replacement"})
    item = {
        "id": "fixture", "name": "Fixture", "version": "1", "platform": "windows-x64",
        "archive_url": "https://official.example/fixture.zip",
        "sha256": hashlib.sha256(payload).hexdigest(), "archive_bytes": len(payload),
        "executables": ["tool.exe"], "license": "Fixture License",
        "license_url": "https://official.example/license", "source_page": "https://official.example/release",
    }
    supply = ManagedSupply({"fixture": item}, urlopen=lambda *_args, **_kwargs: MemoryResponse(payload))
    root = tmp_path / "managed"
    previous = root / "tools" / "fixture" / "1"
    previous.mkdir(parents=True)
    sentinel = previous / "user-sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(supply, "_verify_directory", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="完整性校验失败"):
        supply.ensure_tool(
            root, "fixture", license_accepted_at="2026-07-19T00:00:00Z", job_id="failed-repair",
            on_progress=lambda *_: None, stop_reason=lambda: None,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (root / ".installer-tmp" / "failed-repair" / "tools" / "fixture").exists()


def test_source_archive_is_manifested_and_detects_later_mutation(tmp_path):
    payload = zip_payload({"repo-commit/source.py": b"print('fixed')\n", "repo-commit/LICENSE": b"license"})
    item = {
        "source_url": "https://official.example/repo/commit.zip", "revision": "a" * 40,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    supply = ManagedSupply(urlopen=lambda *_args, **_kwargs: MemoryResponse(payload))
    source = tmp_path / "bundle" / "source"
    supply.install_source(
        item, source, tmp_path / "work", on_progress=lambda *_: None, stop_reason=lambda: None,
    )
    assert supply.source_valid(source, item)
    (source / "source.py").write_text("print('changed')\n", encoding="utf-8")
    assert not supply.source_valid(source, item)
