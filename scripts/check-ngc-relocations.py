#!/usr/bin/env python3
"""Reject NGC packages produced without the relocations Zehn must apply.

The Ndless loader relocates the executable at runtime.  A hand-written link
command can still produce a valid-looking ARM ELF while discarding all ELF
relocation sections; genzehn then emits only a GOT marker and the handheld
rejects or faults before main().  This check is deliberately dependency-free.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


ZEHN_SIGNATURE = 0x6E68655A
ZEHN_VERSION = 1


def read_elf_sections(path: Path) -> set[str]:
    data = path.read_bytes()
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise ValueError(f"{path}: expected little-endian ELF32")
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    shoff = header[5]
    shentsize = header[10]
    shnum = header[11]
    shstrndx = header[12]
    if shentsize != 40 or shoff + shentsize * shnum > len(data):
        raise ValueError(f"{path}: invalid section table")
    sections = [
        struct.unpack_from("<10I", data, shoff + i * shentsize)
        for i in range(shnum)
    ]
    if shstrndx >= len(sections):
        raise ValueError(f"{path}: invalid section-name table index")
    _, _, _, _, str_off, str_size, _, _, _, _ = sections[shstrndx]
    names = data[str_off : str_off + str_size]
    result: set[str] = set()
    for name_off, _, _, _, _, _, _, _, _, _ in sections:
        if name_off >= len(names):
            raise ValueError(f"{path}: invalid section name offset")
        end = names.find(b"\0", name_off)
        if end < 0:
            raise ValueError(f"{path}: unterminated section name")
        result.add(names[name_off:end].decode("ascii"))
    return result


def zehn_relocation_count(path: Path) -> tuple[int, set[int]]:
    data = path.read_bytes()
    for off in range(0, min(len(data), 20 * 1024) - 8, 4):
        signature, version = struct.unpack_from("<II", data, off)
        if signature != ZEHN_SIGNATURE or version != ZEHN_VERSION:
            continue
        file_size, reloc_count, flag_count, extra_size, alloc_size, _ = struct.unpack_from(
            "<6I", data, off + 8
        )
        metadata = 32 + reloc_count * 4 + flag_count * 4 + extra_size
        if file_size > alloc_size or off + file_size != len(data):
            continue
        if off + metadata > len(data):
            continue
        types = {data[off + 32 + i * 4] for i in range(reloc_count)}
        return reloc_count, types
    raise ValueError(f"{path}: no valid embedded Zehn header")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    sections = read_elf_sections(args.elf)
    required = {".rel.text", ".rel.data"}
    missing = required - sections
    if missing:
        raise SystemExit(f"FAIL: {args.elf} is missing ELF relocations: {sorted(missing)}")
    count, types = zehn_relocation_count(args.package)
    # Type 1 is ADD_BASE_GOT.  A linked NGC program should have both its GOT
    # marker and ordinary ADD_BASE relocations from .rel.text/.rel.data.
    if count < 2 or 1 not in types or 0 not in types:
        raise SystemExit(
            f"FAIL: {args.package} has insufficient Zehn relocations: count={count} types={sorted(types)}"
        )
    print(f"PASS: NGC ELF relocations preserved ({len(required)} sections; Zehn relocations={count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
