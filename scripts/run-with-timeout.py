#!/usr/bin/env python3
"""Run a command in an isolated POSIX process group with bounded cleanup."""
from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time


def stop_group(process: subprocess.Popen) -> None:
    # The shell may exit before its USB-owning children. Signal the group even
    # after the direct child exits; never signal the caller's process group.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            # macOS can report EPERM for a group containing only zombies.
            states = subprocess.check_output(
                ["ps", "-axo", "pgid=,stat="], text=True)
            live = [line.split() for line in states.splitlines() if line.strip()]
            if any(int(group) == process.pid and not state.startswith("Z")
                   for group, state in live):
                raise
            break
        if sig == signal.SIGTERM:
            time.sleep(1)
    process.wait()


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run-with-timeout.py SECONDS COMMAND [ARG...]", file=sys.stderr)
        return 2
    try:
        seconds = float(sys.argv[1])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
    except ValueError:
        print("timeout must be a finite positive number", file=sys.stderr)
        return 2
    try:
        process = subprocess.Popen(sys.argv[2:], start_new_session=True)
    except OSError as error:
        print(f"cannot start command: {error}", file=sys.stderr)
        return 127

    def interrupted(signum, _frame):
        stop_group(process)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        return process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        stop_group(process)
        print(f"command timed out after {seconds:g}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
