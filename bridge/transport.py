from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class TransportError(RuntimeError):
    pass


class DirectoryTransport:
    """Local directory transport used for deterministic echo tests."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, name: str) -> Optional[bytes]:
        path = self.root / name
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def write(self, name: str, data: bytes) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


class NLinkTransport:
    """Adapter around the existing N-Link CLI, not a reimplementation of USB."""

    def __init__(self, binary: str = "n-link", remote_dir: str = "/nspireai", timeout: float = 15.0):
        self.binary = binary
        self.remote_dir = remote_dir.rstrip("/")
        self.timeout = timeout
        self.stage = Path(tempfile.mkdtemp(prefix="nspireai-nlink-"))

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.binary, *args],
                cwd=self.stage,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise TransportError(
                f"N-Link executable not found: {self.binary!r}; set N_LINK_BIN"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportError(f"N-Link command timed out after {self.timeout}s") from exc

    def read(self, name: str) -> Optional[bytes]:
        local = self.stage / name
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        remote = f"{self.remote_dir}/{name}"
        result = self._run(["download", remote, str(self.stage)])
        if result.returncode != 0 or not local.exists():
            return None
        return local.read_bytes()

    def write(self, name: str, data: bytes) -> None:
        local = self.stage / name
        local.write_bytes(data)
        result = self._run(["upload", name, self.remote_dir])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise TransportError(f"N-Link upload failed for {name}: {detail}")
