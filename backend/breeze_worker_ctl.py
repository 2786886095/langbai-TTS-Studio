"""Terminate only a registered Studio WSL worker, never an arbitrary PID."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from breeze_worker import identity_matches, process_identity


def terminate_owned(expected: dict, *, grace_seconds: float = 3.) -> dict:
    required = ("pid", "start_time", "pgid", "sid", "boot_id", "cmdline_sha256", "exe", "uid", "token")
    if any(expected.get(key) is None for key in required):
        return {"stopped": False, "reason": "identity_incomplete"}
    pid = int(expected.get("pid", 0))
    if pid <= 1:
        return {"stopped": False, "reason": "invalid_pid"}
    if expected.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        return {"stopped": True, "reason": "wsl_rebooted"}
    try:
        actual = process_identity(pid, expected.get("token"))
    except FileNotFoundError:
        return {"stopped": True, "reason": "already_exited"}
    except (OSError, ValueError, TypeError):
        return {"stopped": False, "reason": "identity_unverifiable"}
    if actual.get("state") == "Z" and expected.get("start_time") == actual.get("start_time"):
        return {"stopped": True, "reason": "exited_zombie"}
    if not identity_matches(expected, actual) or actual["pgid"] != pid or actual["sid"] != pid:
        return {"stopped": False, "reason": "identity_mismatch"}
    # On supported kernels pin the process identity. Refuse a PID-only kill fallback.
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return {"stopped": False, "reason": "pidfd_unavailable"}
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return {"stopped": True, "reason": "already_exited"}
    try:
        if not identity_matches(expected, process_identity(pid, expected["token"])):
            return {"stopped": False, "reason": "identity_changed"}
        # Stop the leader through its pinned pidfd before signalling the owned group.
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        if not identity_matches(expected, process_identity(pid, expected["token"])):
            return {"stopped": False, "reason": "identity_changed"}
        os.killpg(pid, signal.SIGTERM)
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        until = time.monotonic() + grace_seconds
        while time.monotonic() < until:
            try:
                observed = process_identity(pid, expected["token"])
                if observed.get("state") == "Z" or not identity_matches(expected, observed):
                    return {"stopped": True, "reason": "exited"}
            except (FileNotFoundError, ProcessLookupError):
                return {"stopped": True, "reason": "exited"}
            time.sleep(.05)
        # Revalidate before KILL; no action if the registered leader has disappeared.
        if identity_matches(expected, process_identity(pid, expected["token"])):
            os.killpg(pid, signal.SIGKILL)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            try:
                if process_identity(pid, expected["token"]).get("state") == "Z":
                    return {"stopped": True, "reason": "killed"}
            except (FileNotFoundError, ProcessLookupError, ValueError):
                return {"stopped": True, "reason": "killed"}
            time.sleep(.05)
        return {"stopped": False, "reason": "exit_unconfirmed"}
    except ProcessLookupError:
        return {"stopped": True, "reason": "exited"}
    except (OSError, ValueError):
        return {"stopped": False, "reason": "identity_unverifiable"}
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["terminate"])
    parser.parse_args()
    expected = json.loads(sys.stdin.readline())
    print(json.dumps(terminate_owned(expected)))
