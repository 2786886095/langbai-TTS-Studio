"""Run an official engine training script while preserving graceful Windows stop signals."""

from __future__ import annotations

import argparse
import os
import runpy
import signal
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    script = project / "scripts" / "train_voxcpm_finetune.py"
    if not script.is_file():
        raise SystemExit(f"VoxCPM2 training script not found: {script}")

    # CREATE_NEW_PROCESS_GROUP lets the desktop app send CTRL_BREAK_EVENT to this
    # hidden process. Translate it into SIGINT so VoxCPM's official checkpoint
    # handler can save the latest state before exiting.
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, lambda *_: os.kill(os.getpid(), signal.SIGINT))

    os.chdir(project)
    sys.path.insert(0, str(project / "src"))
    sys.argv = [str(script), "--config_path", str(Path(args.config).resolve())]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
