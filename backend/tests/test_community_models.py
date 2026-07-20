import io
import json
import zipfile

import pytest

from app.community_models import (
    CommunityCatalogError,
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
