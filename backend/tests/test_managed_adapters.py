import json
from pathlib import Path

from app.installer import integrity as integrity_module
from app.adapters.subprocess_adapter import build_default_adapters
from app.installer.catalog import ENGINE_TOOL_REQUIREMENTS, INSTALLER_CATALOG, MANAGED_TOOL_CATALOG
from app.installer.integrity import ModelIntegrityVerifier, content_manifest
from app.installer.supply import _directory_manifest


def create_bundle(root: Path, engine: str) -> tuple[Path, Path]:
    bundle = root / "installations" / engine
    source = bundle / "source"
    python = bundle / "env" / "Scripts" / "python.exe"
    source.mkdir(parents=True)
    refresh_source_manifest(source, engine)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    (bundle / "installation.json").write_text(json.dumps({
        "revision": INSTALLER_CATALOG[engine]["revision"],
        "source_archive_sha256": INSTALLER_CATALOG[engine]["sha256"],
    }), encoding="utf-8")
    for tool_id in ENGINE_TOOL_REQUIREMENTS[engine]:
        item = MANAGED_TOOL_CATALOG[tool_id]
        tool_root = root / "tools" / tool_id / item["version"]
        for relative in item["executables"]:
            executable = tool_root / relative
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"fixture")
        tool_digest, tool_bytes = _directory_manifest(tool_root, "tool-manifest.json")
        (tool_root / "tool-manifest.json").write_text(json.dumps({
            "id": tool_id, "version": item["version"], "archiveSha256": item["sha256"],
            "contentManifestSha256": tool_digest, "installedBytes": tool_bytes,
        }), encoding="utf-8")
    model = root / "models" / engine / INSTALLER_CATALOG[engine]["models"][0]["id"]
    model.mkdir(parents=True)
    return source, model


def refresh_source_manifest(source: Path, engine: str) -> None:
    (source / "source-manifest.json").unlink(missing_ok=True)
    source_digest, source_bytes = _directory_manifest(source, "source-manifest.json")
    (source / "source-manifest.json").write_text(json.dumps({
        "revision": INSTALLER_CATALOG[engine]["revision"],
        "archiveSha256": INSTALLER_CATALOG[engine]["sha256"],
        "contentManifestSha256": source_digest,
        "installedBytes": source_bytes,
    }), encoding="utf-8")


def finalize_model(model: Path, engine: str) -> None:
    model_info = INSTALLER_CATALOG[engine]["models"][0]
    digest, installed_bytes = content_manifest(model)
    (model / "model-manifest.json").write_text(json.dumps({
        "revision": model_info["revision"],
        "content_manifest_sha256": digest,
        "installed_bytes": installed_bytes,
    }), encoding="utf-8")


def test_managed_index_and_voxcpm_paths_are_injected(tmp_path):
    root = tmp_path / "managed"
    index_source, index_model = create_bundle(root, "indextts2")
    for name in ("config.yaml", "gpt.pth", "s2mel.pth"):
        (index_model / name).write_bytes(b"fixture")
    finalize_model(index_model, "indextts2")
    vox_source, vox_model = create_bundle(root, "voxcpm")
    (vox_model / "config.json").write_text("{}", encoding="utf-8")
    finalize_model(vox_model, "voxcpm")

    adapters = build_default_adapters(tmp_path / "logs", root)
    index = adapters["indextts2"].status()
    vox = adapters["voxcpm"].status()

    assert index["managed"] and Path(index["project_path"]) == index_source
    assert Path(index["parameter_defaults"]["model_dir"]) == index_model
    assert index["state"] == "installed"
    assert vox["managed"] and Path(vox["project_path"]) == vox_source
    assert Path(vox["parameter_defaults"]["model_path"]) == vox_model
    assert vox["parameter_defaults"]["local_files_only"] is True


def test_managed_gpt_reports_explicit_voice_weight_requirement(tmp_path):
    root = tmp_path / "managed"
    source, model = create_bundle(root, "gpt_sovits")
    config = source / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    refresh_source_manifest(source, "gpt_sovits")
    (model / "chinese-roberta-wwm-ext-large").mkdir()
    (model / "chinese-hubert-base").mkdir()
    finalize_model(model, "gpt_sovits")

    status = build_default_adapters(tmp_path / "logs", root)["gpt_sovits"].status()

    assert status["managed"]
    assert status["state"] == "configuration_required"
    assert status["required_parameters"] == ["t2s_weights_path", "vits_weights_path"]
    assert Path(status["parameter_defaults"]["tts_config_path"]) == config
    assert "明确选择" in status["detail"]


def test_explicit_environment_paths_override_managed_bundle(monkeypatch, tmp_path):
    root = tmp_path / "managed"
    _, model = create_bundle(root, "indextts2")
    for name in ("config.yaml", "gpt.pth", "s2mel.pth"):
        (model / name).write_bytes(b"fixture")
    finalize_model(model, "indextts2")
    explicit_project = tmp_path / "explicit-source"
    explicit_python = tmp_path / "explicit-python.exe"
    explicit_project.mkdir()
    explicit_python.write_bytes(b"fixture")
    monkeypatch.setenv("LANGBAI_INDEXTTS2_PROJECT", str(explicit_project))
    monkeypatch.setenv("LANGBAI_INDEXTTS2_PYTHON", str(explicit_python))

    status = build_default_adapters(tmp_path / "logs", root)["indextts2"].status()

    assert Path(status["project_path"]) == explicit_project
    assert Path(status["python_path"]) == explicit_python
    assert Path(status["parameter_defaults"]["model_dir"]) == model


def test_model_integrity_full_hash_is_cached_until_metadata_changes(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    weights = model / "weights.bin"
    weights.write_bytes(b"verified-model")
    revision = "test-revision"
    digest, installed_bytes = content_manifest(model)
    (model / "model-manifest.json").write_text(json.dumps({
        "revision": revision,
        "content_manifest_sha256": digest,
        "installed_bytes": installed_bytes,
    }), encoding="utf-8")

    calls = 0
    original = integrity_module.content_manifest

    def counted_content_manifest(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(integrity_module, "content_manifest", counted_content_manifest)
    verifier = ModelIntegrityVerifier()

    assert verifier.verify(model, revision)
    assert verifier.verify(model, revision)
    assert calls == 1

    weights.write_bytes(b"changed-model-content")
    assert not verifier.verify(model, revision)
    assert calls == 2
