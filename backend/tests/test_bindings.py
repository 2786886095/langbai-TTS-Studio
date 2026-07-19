from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.mock import MockAdapter
from app.bindings import EngineBindingStore
from app.main import create_app


class ExistingAdapter(MockAdapter):
    def __init__(self, engine_id: str, source: Path, python: Path, runtime: Path):
        super().__init__(engine_id)
        self.source = source
        self.python = python
        self.runtime = runtime

    def status(self):
        return {
            "id": self.engine_id,
            "available": True,
            "state": "installed",
            "managed": False,
            "project_path": str(self.source),
            "python_path": str(self.python),
            "runtime_root": str(self.runtime),
            "parameter_defaults": {},
            "configuration_required": False,
        }


def _existing_engine(tmp_path: Path, engine: str) -> ExistingAdapter:
    source = tmp_path / f"{engine}-source"
    runtime = source if engine != "gpt_sovits" else tmp_path / "gpt-runtime"
    source.mkdir(parents=True)
    runtime.mkdir(parents=True, exist_ok=True)
    python = tmp_path / f"{engine}-python.exe"
    python.write_bytes(b"fixture")
    if engine == "indextts2":
        (source / "indextts").mkdir()
        (source / "checkpoints").mkdir()
    elif engine == "voxcpm":
        (source / "src").mkdir()
        (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    else:
        (source / "GPT_SoVITS").mkdir()
        config = runtime / "GPT_SoVITS" / "configs"
        config.mkdir(parents=True)
        (config / "tts_infer.yaml").write_text("version: v2\n", encoding="utf-8")
    return ExistingAdapter(engine, source, python, runtime)


def test_scan_binds_existing_engines_and_installation_status_uses_same_origin(tmp_path: Path):
    adapters = {engine: _existing_engine(tmp_path, engine) for engine in ("indextts2", "voxcpm", "gpt_sovits")}
    app = create_app(adapters=adapters, data_dir=tmp_path / "data", mock_mode=True)
    with TestClient(app) as client:
        scan = client.post("/api/installations/scan-local")
        assert scan.status_code == 200
        assert scan.json()["found"] == 3
        rows = client.get("/api/installations").json()

    assert {row["engine"] for row in rows} == set(adapters)
    assert all(row["origin"] == "bound" for row in rows)
    assert all(row["installed"] for row in rows)
    assert all(row["source"]["state"] == "bound" for row in rows)
    assert all(row["environment"]["state"] == "bound" for row in rows)
    assert (tmp_path / "data" / "engine-bindings.json").is_file()


def test_discovery_finds_three_existing_projects_under_a_selected_parent(tmp_path: Path):
    index = tmp_path / "indextts2"
    (index / "indextts").mkdir(parents=True)
    (index / "checkpoints").mkdir()
    (index / ".venv" / "Scripts").mkdir(parents=True)
    (index / ".venv" / "Scripts" / "python.exe").write_bytes(b"fixture")

    vox = tmp_path / "VoxCPM"
    (vox / "src").mkdir(parents=True)
    (vox / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (vox / "voxcpm_env" / "Scripts").mkdir(parents=True)
    (vox / "voxcpm_env" / "Scripts" / "python.exe").write_bytes(b"fixture")

    gpt_source = tmp_path / "GPT-SoVITS"
    (gpt_source / "GPT_SoVITS").mkdir(parents=True)
    gpt_runtime = tmp_path / "GPT-SoVITS-runtime"
    (gpt_runtime / "runtime").mkdir(parents=True)
    (gpt_runtime / "runtime" / "python.exe").write_bytes(b"fixture")
    config = gpt_runtime / "GPT_SoVITS" / "configs"
    config.mkdir(parents=True)
    (config / "tts_infer.yaml").write_text("version: v2\n", encoding="utf-8")

    discovered = EngineBindingStore(tmp_path / "bindings.json").discover([str(tmp_path)], max_depth=2)
    assert set(discovered) == {"indextts2", "voxcpm", "gpt_sovits"}
    assert Path(discovered["gpt_sovits"].runtime_root) == gpt_runtime
