"""Persistent Ndless/NavNet bridge.

The helper owns one libnspire USB handle for its whole lifetime.  This process
only translates framed NavNet messages to the selected backend; it never
uploads or downloads a TI document.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .bridge import make_backend
from .protocol import FragmentReassembler, decode, encode, fragment

OP_PING, OP_PONG = 1, 2
OP_REQUEST, OP_RESPONSE, OP_ERROR = 3, 4, 5
OP_CANCEL, OP_NEW = 6, 7
OP_FRAGMENT = 8
# Must match MAX_RESPONSE in src/program/main.c (UTF-8 bytes, not characters).
MAX_RESPONSE_BYTES = 16384


class NavNetBridge:
    def __init__(self, helper: str, backend_name: str, model: str, base_url: Optional[str], timeout: float):
        # Backend configuration can fail (for example, a missing OpenAI key).
        # Do that before opening the USB helper so a startup error cannot leave
        # a Java process holding the calculator interface.
        self.backend = make_backend(backend_name, model, os.environ.get("OPENAI_API_KEY"), base_url, timeout)
        self.process = subprocess.Popen(
            [helper, "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.processed: set[tuple[int, int]] = set()
        self.canceled: set[tuple[int, int]] = set()
        self.conversation_id: Optional[int] = None
        self.fragments = FragmentReassembler()
        self.send_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nspire-ai")
        self.in_flight: set[tuple[int, int]] = set()
        self.backend_lock = threading.Lock()
        self._closed = False

    def send(self, frame: bytes) -> None:
        if not hasattr(self, "send_lock"):
            self.send_lock = threading.Lock()
        if self.process.stdin is None:
            raise RuntimeError("helper stdin is closed")
        with self.send_lock:
            self.process.stdin.write("SEND " + frame.hex() + "\n")
            self.process.stdin.flush()

    def send_message(self, opcode: int, request_id: int, conversation_id: int, payload: bytes) -> None:
        for frame in fragment(opcode, request_id, conversation_id, payload):
            self.send(frame)

    def run(self) -> int:
        if self.process.stdout is None:
            raise RuntimeError("helper stdout is closed")
        service_id = os.environ.get("NSPIRE_SERVICE_ID", "0x5001")
        print(f"navnet bridge starting: service={service_id}; waiting for helper and calculator", flush=True)
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                if not line.startswith("RX "):
                    # Preserve helper lifecycle diagnostics. Previously these
                    # were silently discarded, making READY/CONNECTED impossible
                    # to distinguish from a dead USB path.
                    print("helper: " + line, file=sys.stderr, flush=True)
                    continue
                try:
                    frame = bytes.fromhex(line[3:])
                    opcode, request_id, conversation_id, payload = decode(frame)
                    print(f"RX opcode={opcode} request={request_id} conversation={conversation_id} bytes={len(payload)}", flush=True)
                    self.handle_frame(frame)
                except Exception as exc:
                    print(f"navnet frame error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return self.process.wait()
        finally:
            self.close()

    def close(self) -> None:
        """Stop the helper and model worker exactly once.

        The bridge is normally stopped with Ctrl-C or SIGTERM from a shell
        script.  Without explicit child cleanup, the Java/Rosetta helper can
        survive its Python parent and keep the TI service/USB interface busy.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.executor.shutdown(wait=True, cancel_futures=False)
        finally:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()

    def handle_frame(self, frame: bytes) -> None:
        """Decode one calculator frame and dispatch complete logical messages."""
        opcode, request_id, conversation_id, payload = decode(frame)
        if opcode == OP_FRAGMENT:
            complete = self.fragments.add(conversation_id, request_id, payload)
            if complete is None:
                return
            opcode, payload = complete
        self.handle(opcode, request_id, conversation_id, payload)

    def handle(self, opcode: int, request_id: int, conversation_id: int, payload: bytes) -> None:
        if not hasattr(self, "in_flight"):
            self.in_flight = set()
        if not hasattr(self, "executor"):
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nspire-ai")
        if not hasattr(self, "backend_lock"):
            self.backend_lock = threading.Lock()
        key = (conversation_id, request_id)
        if opcode == OP_PING:
            self.send_message(OP_PONG, request_id, conversation_id, b"PONG")
            return
        if opcode == OP_CANCEL:
            self.canceled.add(key)
            return
        if opcode == OP_NEW:
            # Invalidate work from the old conversation; its late response
            # must never be displayed in the new chat.
            self.canceled.update(self.in_flight)
            reset = getattr(self.backend, "reset", None)
            if reset is not None:
                self.executor.submit(self._reset_backend, reset)
            self.conversation_id = conversation_id
            return
        if opcode != OP_REQUEST or key in self.processed or key in self.canceled or key in self.in_flight:
            return
        if self.conversation_id != conversation_id:
            reset = getattr(self.backend, "reset", None)
            if reset is not None:
                self.executor.submit(self._reset_backend, reset)
            self.conversation_id = conversation_id
        self.in_flight.add(key)
        self.executor.submit(self._answer, key, payload)

    def _reset_backend(self, reset) -> None:
        # Run after earlier answers, without blocking the USB receive loop.
        with self.backend_lock:
            reset()

    def _answer(self, key: tuple[int, int], payload: bytes) -> None:
        conversation_id, request_id = key
        if key in self.canceled or conversation_id != self.conversation_id:
            self.in_flight.discard(key)
            return
        try:
            prompt = payload.decode("utf-8")
            with self.backend_lock:
                answer = self.backend.answer(prompt).encode("utf-8", errors="replace")
        except Exception as exc:
            opcode = OP_ERROR
            answer = f"{type(exc).__name__}: {exc}".encode()
        else:
            opcode = OP_RESPONSE
        if len(answer) > MAX_RESPONSE_BYTES:
            opcode = OP_ERROR
            answer = b"Response exceeds 16384 bytes; ask for a shorter answer."
        try:
            if key not in self.canceled and conversation_id == self.conversation_id:
                self.send_message(opcode, request_id, conversation_id, answer)
            self.processed.add(key)
        finally:
            self.in_flight.discard(key)


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
    def stop(_signum, _frame):
        bridge.close()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
