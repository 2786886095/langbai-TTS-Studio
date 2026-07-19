from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


def _powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)


def _descendants(root_pid: int) -> set[int]:
    script = f"$all = Get-CimInstance Win32_Process; $todo = @({root_pid}); $seen = @(); while ($todo.Count -gt 0) {{ $p = $todo[0]; $todo = @($todo | Select-Object -Skip 1); $kids = @($all | Where-Object ParentProcessId -eq $p | Select-Object -ExpandProperty ProcessId); $seen += $kids; $todo += $kids }}; $seen | Sort-Object -Unique"
    result = _powershell(script)
    return {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}


def _alive(pids: set[int]) -> set[int]:
    if not pids:
        return set()
    joined = ",".join(str(pid) for pid in sorted(pids))
    result = _powershell(f"Get-Process -Id {joined} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id")
    return {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}


def test_packaged_app_closes_its_complete_process_tree(packaged_evidence: bool, packaged_exe: Path | None) -> None:
    if not packaged_evidence:
        pytest.skip("packaged cleanup runs with --packaged-evidence")
    if packaged_exe is None:
        pytest.skip("包内进程清理需要单独传入 --packaged-exe；tag 发布流水线必须运行该阶段")
    assert packaged_exe.is_file(), f"打包程序不存在: {packaged_exe}"
    env = {**os.environ, "LANGBAI_TTS_MOCK": "1", "LANGBAI_ACCEPTANCE_PACKAGED": "1"}
    process = subprocess.Popen([str(packaged_exe)], cwd=packaged_exe.parent, env=env)
    descendants: set[int] = set()
    try:
        time.sleep(8)
        assert process.poll() is None, f"打包程序启动后提前退出，exit={process.returncode}"
        descendants = _descendants(process.pid)
        closed = _powershell(f"$p = Get-Process -Id {process.pid} -ErrorAction Stop; if (-not $p.CloseMainWindow()) {{ exit 3 }}")
        assert closed.returncode == 0, "无法通过正常窗口关闭路径触发 Electron before-quit"
        process.wait(timeout=20)
        deadline = time.time() + 10
        remaining = _alive(descendants)
        while remaining and time.time() < deadline:
            time.sleep(0.5)
            remaining = _alive(descendants)
        assert not remaining, f"关闭打包程序后仍有子进程残留: {sorted(remaining)}"
    finally:
        remaining = _alive({process.pid, *descendants})
        for pid in sorted(remaining, reverse=True):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
