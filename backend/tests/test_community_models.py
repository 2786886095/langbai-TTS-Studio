import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.community_models as community_models_module
from app.community_models import (
    CommunityCatalogError,
    CommunityInstallRequest,
    CommunityModelManager,
    _safe_archive_members,
    _validate_download_url,
)


class FakeCatalog:
    def categories(self):
        return ["原神", "星穹铁道"]

    def languages(self, category):
        assert category == "原神"
        return ["中文", "日语"]

    def models(self, category, language):
        return [{"name": "测试角色", "downloadUrl": "https://pan.acgnai.top/test.zip"}]


def make_zip(rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in rows:
            archive.writestr(name, content)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def make_zip_bytes(rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in rows:
            archive.writestr(name, content)
    return buffer.getvalue()


class FakeDownloadResponse:
    def __init__(self, payload):
        self.payload = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return "https://pan.acgnai.top/test.zip"

    def read(self, size=-1):
        return self.payload.read(size)


def test_catalog_rows_include_source_and_install_state(tmp_path):
    manager = CommunityModelManager(tmp_path / "community", FakeCatalog())
    assert manager.categories() == ["原神", "星穹铁道"]
    assert manager.languages("原神") == ["中文", "日语"]

    row = manager.models("原神", "中文")[0]
    assert row["name"] == "测试角色"
    assert row["sourcePage"].startswith("https://")
    assert row["installed"] is None

    target = tmp_path / "community" / "installed" / "v4" / "test"
    target.mkdir(parents=True)
    manifest = {
        "name": "测试角色",
        "category": "原神",
        "language": "中文",
        "installPath": str(target.resolve()),
        "installedAt": "2026-07-19T00:00:00Z",
    }
    (target / "langbai-model.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    installed = manager.models("原神", "中文")[0]["installed"]
    assert installed["installPath"] == str(target.resolve())


def test_archive_filter_keeps_only_model_assets_and_docs():
    with make_zip([
        ("model/voice.ckpt", b"gpt"),
        ("model/voice.pth", b"sovits"),
        ("reference/ref.wav", b"audio"),
        ("README.md", b"docs"),
        ("run.exe", b"unsafe"),
        ("install.ps1", b"unsafe"),
    ]) as archive:
        accepted = _safe_archive_members(archive)
    assert {item.filename for item in accepted} == {
        "model/voice.ckpt", "model/voice.pth", "reference/ref.wav", "README.md",
    }


def test_archive_rejects_path_traversal_even_for_ignored_files():
    with make_zip([("../escape.exe", b"no")]) as archive:
        with pytest.raises(CommunityCatalogError, match="越界路径"):
            _safe_archive_members(archive)


def test_download_host_allowlist():
    _validate_download_url("https://pan.acgnai.top/model.zip")
    _validate_download_url("https://cdn-lfs-cn-1.modelscope.cn/model.zip")
    _validate_download_url("https://cdn-lfs-cn-12.modelscope.cn/model.zip")
    with pytest.raises(CommunityCatalogError, match="不受信任"):
        _validate_download_url("https://example.com/model.zip")
    with pytest.raises(CommunityCatalogError, match="不受信任"):
        _validate_download_url("https://cdn-lfs-cn-1.modelscope.cn.example.com/model.zip")
    with pytest.raises(CommunityCatalogError, match="不受信任"):
        _validate_download_url("http://pan.acgnai.top/model.zip")


def test_install_creates_version_directory_before_atomic_move(tmp_path, monkeypatch):
    archive = make_zip_bytes([
        ("weights/voice.ckpt", b"gpt"),
        ("weights/voice.pth", b"sovits"),
        ("reference/ref.wav", b"audio"),
    ])

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == 60
            return FakeDownloadResponse(archive)

    manager = CommunityModelManager(tmp_path / "community", FakeCatalog())
    monkeypatch.setattr(community_models_module, "threading", SimpleNamespace(Thread=ImmediateThread))
    monkeypatch.setattr(community_models_module, "build_opener", lambda *_handlers: FakeOpener())

    manager.install(CommunityInstallRequest(
        category="原神", language="中文", modelName="测试角色", version="auto", licenseAccepted=True,
    ))

    job = manager.list_jobs()[0]
    assert job.status == "completed"
    assert job.installed_model is not None
    install_path = Path(job.installed_model["installPath"])
    assert install_path.parent == manager.installed_root / "auto"
    assert (install_path / "weights" / "voice.ckpt").read_bytes() == b"gpt"
    assert (install_path / "weights" / "voice.pth").read_bytes() == b"sovits"
    assert (install_path / "langbai-model.json").is_file()
