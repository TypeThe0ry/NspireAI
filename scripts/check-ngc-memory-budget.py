#!/usr/bin/env python3
"""Keep the standalone NGC image below the CX II loader memory budget."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


SIGNATURE = 0x6E68655A


def find_header(data: bytes) -> tuple[int, int, int]:
    for off in range(0, min(len(data), 20 * 1024) - 8, 4):
        if struct.unpack_from("<II", data, off) != (SIGNATURE, 1):
            continue
        file_size, reloc_count, flag_count, extra_size, alloc_size, _ = struct.unpack_from(
            "<6I", data, off + 8
        )
        metadata = 32 + reloc_count * 4 + flag_count * 4 + extra_size
        if file_size <= alloc_size and off + metadata + (file_size - metadata) == len(data):
            return off, file_size, alloc_size
    raise SystemExit("FAIL: no valid embedded Zehn header")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--max-alloc", type=int, default=60000)
    args = parser.parse_args()
    _, file_size, alloc_size = find_header(args.package.read_bytes())
    if alloc_size > args.max_alloc:
        raise SystemExit(
            f"FAIL: {args.package} needs {alloc_size} bytes; budget is {args.max_alloc}"
        )
    print(f"PASS: NGC loader allocation {alloc_size} bytes <= budget {args.max_alloc} (file={file_size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
