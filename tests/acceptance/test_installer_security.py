from __future__ import annotations

import ast
import json
from pathlib import Path


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def test_installer_baseline_locks_exact_official_commit_archives(project_root: Path) -> None:
    path = project_root / "tests" / "acceptance" / "installer_catalog_baseline.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "indextts2": "https://codeload.github.com/index-tts/index-tts/zip/13495845e3028f0bb6ca1462ad22aa0e76349e40",
        "voxcpm": "https://codeload.github.com/OpenBMB/VoxCPM/zip/616d3d3e630a9c96c2853250eef91b0f39dcd5fa",
        "gpt_sovits": "https://codeload.github.com/RVC-Boss/GPT-SoVITS/zip/be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2",
    }
    actual = {key: value["source_url"] for key, value in baseline["engines"].items()}
    assert actual == expected
    for engine in baseline["engines"].values():
        assert len(engine["source_commit"]) == 40
        assert len(engine["sha256"]) == 64
        assert engine["source_commit"] in engine["source_url"]
        assert "latest" not in engine["source_url"].lower()


def test_installer_manager_does_not_probe_or_invoke_system_git(project_root: Path) -> None:
    manager = (project_root / "backend" / "app" / "installer" / "manager.py").read_text(encoding="utf-8").lower()
    assert "git clone" not in manager
    assert "shutil.which" not in manager
    assert 'self._tool("git")' not in manager


def test_python_backend_never_enables_subprocess_shell(project_root: Path) -> None:
    backend = project_root / "backend"
    violations: list[str] = []
    for path in backend.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) not in {"run", "Popen", "call", "check_call", "check_output"}:
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    violations.append(f"{path}:{node.lineno}")
    assert not violations, f"shell=True is forbidden: {violations}"


def test_installer_code_does_not_embed_command_shell_wrappers(project_root: Path) -> None:
    installer_files = [
        path
        for path in (project_root / "backend").rglob("*.py")
        if "install" in path.name.lower() and ".venv" not in path.parts
    ]
    forbidden = ("cmd /c", "powershell -command", "pwsh -command")
    violations = []
    for path in installer_files:
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token in text:
                violations.append(f"{path}:{token}")
    assert not violations, f"Installer contains a shell wrapper: {violations}"
