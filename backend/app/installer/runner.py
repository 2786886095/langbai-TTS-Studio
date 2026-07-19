from __future__ import annotations

import os
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class CommandStopped(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class CommandResult:
    returncode: int
    stdout: str


class CommandRunner:
    """Argument-vector process runner. It intentionally has no shell mode."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path,
        env: dict[str, str] | None,
        stop_reason: Callable[[], str | None],
        on_output: Callable[[str], None],
    ) -> CommandResult:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("command must be a non-empty string argument vector")
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=process_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.rstrip("\r\n"))
            lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        output: list[str] = []
        stream_done = False
        while process.poll() is None or not stream_done:
            reason = stop_reason()
            if reason:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise CommandStopped(reason)
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                stream_done = True
            else:
                output.append(line)
                on_output(line)
        returncode = process.wait()
        combined = "\n".join(output)
        if returncode != 0:
            tail = "\n".join(output[-30:])
            raise RuntimeError(f"command exited with code {returncode}: {tail}")
        return CommandResult(returncode=returncode, stdout=combined)
