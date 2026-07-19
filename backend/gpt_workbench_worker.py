from __future__ import annotations

import argparse
import os
import runpy
import sys
import webbrowser
from pathlib import Path


def _block_external_browser(*_args, **_kwargs) -> bool:
    """Keep the official Gradio workbench inside langbai TTS Studio."""
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--language", default="zh_CN")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    script = project / "webui.py"
    if not script.is_file():
        raise FileNotFoundError(f"GPT-SoVITS webui.py not found: {script}")

    # GPT-SoVITS currently calls Gradio with inbrowser=True. Intercept the
    # standard-library browser hooks so starting the local server never opens
    # Edge/Chrome. The local URL remains available to the Electron iframe.
    webbrowser.open = _block_external_browser
    webbrowser.open_new = _block_external_browser
    webbrowser.open_new_tab = _block_external_browser

    os.chdir(project)
    sys.path.insert(0, str(project))
    sys.argv = [str(script), args.language]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
