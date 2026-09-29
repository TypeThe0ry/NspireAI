"""Persistent Ndless/NavNet bridge.

The helper owns one libnspire USB handle for its whole lifetime.  This process
only translates framed NavNet messages to the selected backend; it never
uploads or downloads a TI document.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .bridge import make_backend, split_think_prefix
from .protocol import (
    OP_ACTION,
    OP_DUMP,
    OP_DUMP_REQ,
    OP_HELLO,
    OP_INJECT,
    FragmentReassembler,
    decode,
    encode,
    fragment,
)

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
        # Protocol 2 (thin-terminal page).  Created on the first HELLO; when
        # the rendering modules are unavailable the page gets plain text.
        self.page_host = None
        # Menu and session actions must stay responsive while a model call
        # occupies the answer worker.
        self.ui_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nspire-ui")
        self.page_host_error: Optional[str] = None
        self.page_conversation: Optional[int] = None
        self.telemetry = ""
        self.telemetry_at = 0.0
        self.dump_waiters: dict[int, tuple[threading.Event, list]] = {}
        self.control = None

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
                    # PING/PONG now flow every second as keepalive; only log
                    # frames that carry chat traffic.
                    if opcode == OP_PONG:
                        # Page telemetry rides on the PONG payload; it
                        # arrives with every keepalive, so keep only the
                        # latest (shown by the control "status" command).
                        self.telemetry = payload.decode("ascii", "replace")
                        self.telemetry_at = time.monotonic()
                    elif opcode not in (OP_PING, OP_FRAGMENT):
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
        if self.control is not None:
            server, path = self.control
            threading.Thread(target=server.shutdown, daemon=True).start()
            try:
                path.unlink()
            except OSError:
                pass
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        # Give the helper a moment to see EOF and run its own NavNet
        # disconnect; a hard terminate can leave the calculator holding a
        # half-open session until its liveness timeout.
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.ui_executor.shutdown(wait=False, cancel_futures=True)
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
        if opcode == OP_PONG:
            return
        if opcode == OP_HELLO:
            self.page_conversation = conversation_id
            self.ui_executor.submit(self._page_call, "on_hello", conversation_id, payload)
            return
        if opcode == OP_ACTION:
            self.ui_executor.submit(self._page_call, "on_action", payload)
            return
        if opcode == OP_DUMP:
            waiter = getattr(self, "dump_waiters", {}).pop(request_id, None)
            if waiter is not None:
                waiter[1].append(payload)
                waiter[0].set()
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
        if getattr(self, "page_conversation", None) == conversation_id and self._page_host() is not None:
            self.executor.submit(self._page_request, key, payload)
        else:
            self.executor.submit(self._answer, key, payload)

    # ----- protocol 2: thin-terminal page -----------------------------------

    def _page_host(self):
        """The page host, or None when rendering is unavailable."""
        if self.page_host is None and self.page_host_error is None:
            try:
                from .pagehost import PageHost

                self.page_host = PageHost(self.send_message, self.backend)
            except Exception as exc:
                self.page_host_error = f"{type(exc).__name__}: {exc}"
                print(f"page host unavailable, using plain text: {self.page_host_error}",
                      file=sys.stderr, flush=True)
        return self.page_host

    def _page_call(self, method: str, *args) -> None:
        host = self._page_host()
        if host is None:
            return
        try:
            getattr(host, method)(*args)
        except Exception as exc:
            print(f"page host {method} failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)

    def _page_request(self, key: tuple[int, int], payload: bytes) -> None:
        conversation_id, request_id = key
        try:
            with self.backend_lock:
                self.page_host.on_request(request_id, payload)
            self.processed.add(key)
        except Exception as exc:
            print(f"page request failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            reason = f"{type(exc).__name__}: {exc}".encode("ascii", "replace")[:200]
            self.send_message(OP_ERROR, request_id, conversation_id, reason)
        finally:
            self.in_flight.discard(key)

    # ----- control socket (tests and tools) ---------------------------------

    def inject(self, data: bytes) -> None:
        """Feed key events to the page (see inject_event in page.c)."""
        conversation = self.page_conversation or 0
        for start in range(0, len(data), 200):
            self.send_message(OP_INJECT, 0, conversation, data[start:start + 200])

    def dump_screen(self, timeout: float = 20.0) -> bytes:
        """Fetch the page's framebuffer as PNG bytes."""
        from .pagehost import png_from_rgb565_runs

        request_id = int(time.monotonic() * 1000) & 0x7FFFFFFF
        event, box = threading.Event(), []
        self.dump_waiters[request_id] = (event, box)
        self.send_message(OP_DUMP_REQ, request_id, self.page_conversation or 0, b"")
        if not event.wait(timeout):
            self.dump_waiters.pop(request_id, None)
            raise TimeoutError("the page did not answer the dump request")
        return png_from_rgb565_runs(box[0])[2]

    def control_command(self, request: dict) -> dict:
        command = request.get("cmd")
        if command == "status":
            age = time.monotonic() - self.telemetry_at if self.telemetry_at else None
            return {"ok": True, "telemetry": self.telemetry, "telemetry_age": age,
                    "page": self.page_conversation is not None,
                    "page_host": self.page_host is not None,
                    "page_host_error": self.page_host_error}
        if command == "inject":
            self.inject(request.get("data", "").encode("latin-1"))
            return {"ok": True}
        if command == "dump":
            png = self.dump_screen(float(request.get("timeout", 20)))
            Path(request["path"]).write_bytes(png)
            return {"ok": True, "bytes": len(png)}
        if command == "block":
            # Synthetic image block, for throughput measurements.
            from PIL import Image, ImageDraw

            from . import imagecodec
            height = int(request.get("height", 120))
            image = Image.new("L", (320, height), 255)
            draw = ImageDraw.Draw(image)
            for y in range(0, height, 12):
                draw.text((4, y), f"row {y}: the quick brown fox 0123456789", fill=0)
            payload = imagecodec.encode_block(0, image, 0, bpp=4)
            started = time.monotonic()
            self.send_message(10, 0, self.page_conversation or 0, payload)
            return {"ok": True, "bytes": len(payload), "seconds": time.monotonic() - started}
        return {"ok": False, "error": f"unknown command {command!r}"}

    def start_control(self, path: Path) -> None:
        bridge = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                try:
                    reply = bridge.control_command(json.loads(line))
                except Exception as exc:
                    reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.wfile.write((json.dumps(reply) + "\n").encode("utf-8"))

        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name="nspire-control", daemon=True).start()
        self.control = (server, path)

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
            effort, prompt = split_think_prefix(payload.decode("utf-8"))
            with self.backend_lock:
                if effort is not None and getattr(self.backend, "supports_effort", False):
                    text = self.backend.answer(prompt, effort=effort)
                else:
                    text = self.backend.answer(prompt)
                answer = text.encode("utf-8", errors="replace")
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
    parser.add_argument("--backend", choices=("echo", "openai", "deepseek"), default="echo")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-timeout", type=float, default=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "180")))
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    bridge = NavNetBridge(args.helper, args.backend, args.model, args.base_url, args.api_timeout)
    home = Path(os.environ.get("NSPIREAI_HOME", str(Path.home() / ".config" / "nspireai")))
    try:
        bridge.start_control(home / "bridge.sock")
    except OSError as exc:
        print(f"control socket unavailable: {exc}", file=sys.stderr, flush=True)
    def stop(_signum, _frame):
        bridge.close()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return bridge.run()


if __name__ == "__main__":
    raise SystemExit(main())
