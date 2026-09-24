#!/usr/bin/env python3
"""Keep the physical USB gate safe under Bash pipefail."""

from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run-navnet-bridge.sh").read_text()
    if "tee /dev/stderr | grep -q" in source:
        raise SystemExit("FAIL: USB gate regressed to tee|grep -q under pipefail")
    if 'USB_STATE="$($ROOT/scripts/check-nspire-usb-state.sh 2>&1)"' not in source:
        raise SystemExit("FAIL: USB gate does not capture the complete state output")
    if 'grep -q \'^STATE=CX2_USB_CANDIDATE \' <<<"$USB_STATE"' not in source:
        raise SystemExit("FAIL: USB gate no longer checks the CX II state line")
    remote = (root / "scripts/run-nspire-remote.sh").read_text()
    for required in (
        'CALLER_CWD="$PWD"',
        'REMOTE_ARGS=("$@")',
        'resolve_local_arg()',
        'NspireRemoteControl "${REMOTE_ARGS[@]}"',
    ):
        if required not in remote:
            raise SystemExit(f"FAIL: remote path handling missing {required}")
    print("PASS: NavNet USB gate and remote path handling are safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
