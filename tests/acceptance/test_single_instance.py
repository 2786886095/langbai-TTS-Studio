from pathlib import Path


def test_windows_desktop_uses_single_instance_lock(project_root: Path) -> None:
    source = (project_root / "electron" / "main.cjs").read_text(encoding="utf-8")
    assert "app.requestSingleInstanceLock()" in source
    assert "if (!ownsSingleInstanceLock) app.quit()" in source
    assert "app.on('second-instance'" in source
    assert "mainWindow.isMinimized()" in source and "mainWindow.restore()" in source
    assert "mainWindow.show()" in source and "mainWindow.focus()" in source
    assert "if (!ownsSingleInstanceLock) return" in source
