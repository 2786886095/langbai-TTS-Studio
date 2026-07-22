from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


ACCEPTANCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ACCEPTANCE_DIR.parents[1]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("langbai acceptance")
    group.addoption(
        "--require-implementation",
        action="store_true",
        help="Fail instead of skip when the application implementation is missing.",
    )
    group.addoption(
        "--live-engines",
        action="store_true",
        help="Run opt-in tests that load the real local TTS engines.",
    )
    group.addoption(
        "--commercial-release",
        action="store_true",
        help="Deprecated alias for --prepackage; retained for existing CI callers.",
    )
    group.addoption(
        "--prepackage",
        action="store_true",
        help="Run strict source-built UI/API checks before creating a Windows package.",
    )
    group.addoption(
        "--packaged-evidence",
        action="store_true",
        help="Run opt-in checks against the packaged Windows application executable.",
    )
    group.addoption(
        "--packaged-exe",
        default=None,
        help="Absolute path to the packaged Windows executable used by process-cleanup acceptance.",
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def parameter_baseline() -> dict:
    path = ACCEPTANCE_DIR / "engine_parameter_baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def require_implementation(pytestconfig: pytest.Config) -> bool:
    return bool(pytestconfig.getoption("--require-implementation"))


@pytest.fixture(scope="session")
def commercial_release(pytestconfig: pytest.Config) -> bool:
    return bool(
        pytestconfig.getoption("--prepackage")
        or pytestconfig.getoption("--commercial-release")
    )


@pytest.fixture(scope="session")
def packaged_evidence(pytestconfig: pytest.Config) -> bool:
    return bool(pytestconfig.getoption("--packaged-evidence"))


@pytest.fixture(scope="session")
def packaged_exe(pytestconfig: pytest.Config) -> Path | None:
    raw = pytestconfig.getoption("--packaged-exe")
    return Path(raw).resolve() if raw else None


def require_path(path: Path, require: bool, purpose: str) -> None:
    if path.exists():
        return
    message = f"Missing {purpose}: {path}"
    if require:
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def backend_app(project_root: Path, require_implementation: bool, tmp_path_factory: pytest.TempPathFactory):
    main_path = project_root / "backend" / "app" / "main.py"
    require_path(main_path, require_implementation, "backend application")

    isolated_root = tmp_path_factory.mktemp("langbai-acceptance")
    os.environ["LANGBAI_TTS_MOCK"] = "1"
    os.environ["LANGBAI_TTS_DATA"] = str(isolated_root / "data")
    os.environ["LANGBAI_OUTPUT_ROOT"] = str(isolated_root / "output")
    sys.path.insert(0, str(project_root / "backend"))
    try:
        from app.main import app  # type: ignore
    except Exception as exc:
        if require_implementation:
            pytest.fail(f"Cannot import backend app in mock mode: {exc!r}")
        pytest.skip(f"Backend app is not importable yet: {exc!r}")
    return app


@pytest.fixture(scope="session")
def api_client(backend_app):
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        pytest.fail(f"FastAPI TestClient is required: {exc!r}")
    with TestClient(backend_app) as client:
        yield client
