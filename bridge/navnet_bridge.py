"""Persistent Ndless/NavNet bridge.

The helper owns one libnspire USB handle for its whole lifetime.  This process
only translates framed NavNet messages to the selected backend; it never
uploads or downloads a TI document.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

from .bridge import make_backend
from .protocol import FragmentReassembler, decode, encode, fragment

OP_PING, OP_PONG = 1, 2
OP_REQUEST, OP_RESPONSE, OP_ERROR = 3, 4, 5
OP_CANCEL, OP_NEW = 6, 7
OP_FRAGMENT = 8


class NavNetBridge:
    def __init__(self, helper: str, backend_name: str, model: str, base_url: Optional[str], timeout: float):
        self.process = subprocess.Popen(
            [helper, "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.backend = make_backend(backend_name, model, os.environ.get("OPENAI_API_KEY"), base_url, timeout)
        self.processed: set[tuple[int, int]] = set()
        self.canceled: set[tuple[int, int]] = set()
        self.conversation_id: Optional[int] = None
        self.fragments = FragmentReassembler()

    def send(self, frame: bytes) -> None:
        if self.process.stdin is None:
            raise RuntimeError("helper stdin is closed")
        self.process.stdin.write("SEND " + frame.hex() + "\n")
        self.process.stdin.flush()

    def send_message(self, opcode: int, request_id: int, conversation_id: int, payload: bytes) -> None:
        for frame in fragment(opcode, request_id, conversation_id, payload):
            self.send(frame)

    def run(self) -> int:
        if self.process.stdout is None:
            raise RuntimeError("helper stdout is closed")
        print("navnet bridge ready: persistent=TI-NavNet service=0x5001", flush=True)
        for line in self.process.stdout:
            line = line.strip()
            if not line.startswith("RX "):
                continue
            try:
                frame = bytes.fromhex(line[3:])
                opcode, request_id, conversation_id, payload = decode(frame)
                if opcode == OP_FRAGMENT:
                    complete = self.fragments.add(conversation_id, request_id, payload)
                    if complete is None:
                        continue
                    opcode, payload = complete
                self.handle(opcode, request_id, conversation_id, payload)
            except Exception as exc:
                print(f"navnet frame error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return self.process.wait()

    def handle(self, opcode: int, request_id: int, conversation_id: int, payload: bytes) -> None:
        key = (conversation_id, request_id)
        if opcode == OP_PING:
            self.send_message(OP_PONG, request_id, conversation_id, b"PONG")
            return
        if opcode == OP_CANCEL:
            self.canceled.add(key)
            return
        if opcode == OP_NEW:
            reset = getattr(self.backend, "reset", None)
            if reset is not None:
                reset()
            self.conversation_id = conversation_id
            return
        if opcode != OP_REQUEST or key in self.processed or key in self.canceled:
            return
        if self.conversation_id != conversation_id:
            reset = getattr(self.backend, "reset", None)
            if reset is not None:
                reset()
            self.conversation_id = conversation_id
        try:
            prompt = payload.decode("utf-8")
            answer = self.backend.answer(prompt).encode("utf-8", errors="replace")
        except Exception as exc:
            opcode = OP_ERROR
            answer = f"{type(exc).__name__}: {exc}".encode()
        else:
            opcode = OP_RESPONSE
        self.send_message(opcode, request_id, conversation_id, answer)
        self.processed.add(key)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent Ndless NavNet AI bridge")
    parser.add_argument("--helper", default=os.environ.get("NSPIRE_USB_HELPER", "bridge/nspire-helper/target/debug/nspireai-usb-helper"))
    parser.add_argument("--backend", choices=("echo", "openai"), default="echo")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-timeout", type=float, default=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "45")))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    bridge = NavNetBridge(args.helper, args.backend, args.model, args.base_url, args.api_timeout)
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
