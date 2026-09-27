#!/usr/bin/env python3
"""Check the opt-in Ndless loader IRQ-boundary patch without building/uploading.

The patch is intentionally kept outside .deps/ndless (which is ignored and may
be a dirty checkout).  This check proves that it applies to the pinned loader,
restores the saved mask immediately before entry(), and does not silently
replace the normal post-entry cleanup restore.
"""

from pathlib import Path
import subprocess


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ndless = root / ".deps/ndless"
    patch = root / "patches/ndless/2026-09-27-restore-loader-irqs-before-entry.patch"
    loader = ndless / "ndless/src/resources/ploaderhook.c"
    if not ndless.is_dir() or not loader.is_file():
        raise SystemExit("FAIL: pinned Ndless checkout is missing")
    text = loader.read_text()
    mask = text.index("TCT_Local_Control_Interrupts(-1)")
    entry = text.index("ret = entry(argc, argv);")
    restore = text.index("TCT_Local_Control_Interrupts(intmask)")
    if not mask < entry < restore:
        raise SystemExit("FAIL: baseline loader ordering changed")
    patch_text = patch.read_text()
    marker = "+\tTCT_Local_Control_Interrupts(intmask);\n \tret = entry(argc, argv);"
    if marker not in patch_text:
        raise SystemExit("FAIL: variant does not restore the saved mask immediately before entry")
    if patch_text.count("TCT_Local_Control_Interrupts(intmask);") != 1:
        raise SystemExit("FAIL: variant patch changes more than the entry boundary")
    check = subprocess.run(
        # The bundled Python is x86_64 on this host; force the native git
        # slice so Apple's xcrun does not resolve the incompatible Rosetta
        # developer-tool slice.
        ["arch", "-arm64", "git", "-C", str(ndless), "apply", "--check", str(patch)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check.returncode:
        raise SystemExit("FAIL: loader variant does not apply cleanly:\n" + check.stderr)
    print("PASS: pinned loader still masks IRQs across the unmodified entry boundary")
    print("PASS: opt-in variant restores the saved mask exactly once before entry()")
    print("PASS: patch applies cleanly; no device upload or runtime replacement performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
