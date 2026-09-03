from pathlib import Path


def test_desktop_rotates_logs_and_suppresses_high_frequency_poll_entries():
    root = Path(__file__).resolve().parents[2]
    source = (root / "electron" / "main.cjs").read_text(encoding="utf-8")

    assert "DESKTOP_LOG_MAX_BYTES" in source
    assert "DESKTOP_LOG_BACKUPS" in source
    assert "fs.renameSync(source, destination)" in source
    assert "DESKTOP_LOG_MAX_BYTES * 4" in source
    assert "fs.rmSync(target, { force: true })" in source
    assert "isRoutinePoll" in source
    assert "engines\\/status|jobs" in source


def test_frontend_has_synchronous_submit_guard():
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "const submitInFlightRef = useRef(false);" in source
    assert "if (submitInFlightRef.current) return;" in source
    assert "submitInFlightRef.current = false;" in source
