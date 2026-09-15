from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional, Protocol


MAX_BODY_BYTES = 256 * 1024

# The CX II N-Link file service accepts TI document extensions.  It silently
# rejects arbitrary .txt/.id names, so the exchange files use .tns suffixes.
REQUEST_ID = "request.id.tns"
REQUEST_BODY = "request.tns"
RESPONSE_ID = "response.id.tns"
RESPONSE_BODY = "response.tns"


class Transport(Protocol):
    def read(self, name: str) -> Optional[bytes]: ...
    def write(self, name: str, data: bytes) -> None: ...


class EchoBackend:
    def answer(self, prompt: str) -> str:
        return f"Mac received: {prompt}"

    def reset(self) -> None:
        pass


class OpenAIBackend:
    def __init__(self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 45.0):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI backend requires the official SDK: python3 -m pip install -r bridge/requirements.txt"
            ) from exc
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["timeout"] = timeout
        self.client = OpenAI(**kwargs)
        self.model = model
        self.messages: list[dict[str, str]] = []

    def reset(self) -> None:
        self.messages.clear()

    def answer(self, prompt: str) -> str:
        self.messages.append({"role": "user", "content": prompt})
        try:
            response = self.client.responses.create(model=self.model, input=self.messages)
            answer = response.output_text
        except Exception:
            # Keep the failed user turn out of the next retry's context.
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": answer})
        return answer


class Bridge:
    """One request at a time; the response ID is uploaded last as a ready marker."""

    def __init__(self, transport: Transport, backend, encoding: str = "utf-8"):
        self.transport = transport
        self.backend = backend
        self.encoding = encoding
        self.processed: set[str] = set()
        self.conversation_key: Optional[str] = None
        self.pending_response: Optional[tuple[str, bytes]] = None

    @staticmethod
    def _conversation_key(request_id: str) -> str:
        # Lua IDs are `session-conversation-counter`; accepting an opaque ID here keeps
        # the transport usable with simple external test producers too.
        return request_id.rsplit("-", 1)[0]

    def step(self) -> Optional[str]:
        if self.pending_response is not None:
            return self._publish_pending()
        request_id_raw = self.transport.read(REQUEST_ID)
        if not request_id_raw:
            return None
        request_id = request_id_raw.decode("ascii", errors="strict").strip()
        if not request_id or len(request_id) > 95 or request_id in self.processed:
            return None

        body_raw = self.transport.read(REQUEST_BODY)
        if body_raw is None:
            return None
        conversation_key = self._conversation_key(request_id)
        if conversation_key != self.conversation_key:
            reset = getattr(self.backend, "reset", None)
            if reset is not None:
                reset()
            self.conversation_key = conversation_key
        try:
            prompt = body_raw.decode(self.encoding)
        except UnicodeDecodeError as exc:
            answer = f"[bridge error] request is not valid UTF-8: {exc}"
        else:
            try:
                answer = self.backend.answer(prompt)
            except Exception as exc:  # never expose API keys or full request headers
                answer = f"[bridge error] {type(exc).__name__}: {exc}"

        answer_bytes = answer.encode(self.encoding, errors="replace")
        if len(answer_bytes) > MAX_BODY_BYTES:
            answer_bytes = (
                f"[bridge error] response exceeds {MAX_BODY_BYTES} bytes; shorten the request."
            ).encode(self.encoding)
        self.pending_response = (request_id, answer_bytes)
        return self._publish_pending()

    def _publish_pending(self) -> str:
        assert self.pending_response is not None
        request_id, answer_bytes = self.pending_response
        self.transport.write(RESPONSE_BODY, answer_bytes)
        # The Lua extension reads response.id.tns as the ready marker. Uploading
        # it after response.tns prevents a new ID from pointing at incomplete text.
        self.transport.write(RESPONSE_ID, request_id.encode("ascii"))
        self.processed.add(request_id)
        self.pending_response = None
        return request_id


def make_backend(name: str, model: str, api_key: Optional[str], base_url: Optional[str], timeout: float):
    if name == "echo":
        return EchoBackend()
    return OpenAIBackend(model=model, api_key=api_key, base_url=base_url, timeout=timeout)


def make_transport(args: argparse.Namespace) -> Transport:
    if args.transport == "dir":
        if not args.transport_dir:
            raise SystemExit("--transport-dir is required with --transport dir")
        from .transport import DirectoryTransport

        return DirectoryTransport(Path(args.transport_dir))
    from .transport import NLinkTransport

    return NLinkTransport(binary=args.n_link_bin, remote_dir=args.remote_dir, timeout=args.n_link_timeout)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TI-Nspire AI file exchange bridge")
    parser.add_argument("--transport", choices=("nlink", "dir"), default="nlink")
    parser.add_argument("--transport-dir", help="local calculator-directory simulator")
    parser.add_argument("--n-link-bin", default=os.environ.get("N_LINK_BIN", "n-link"))
    parser.add_argument("--remote-dir", default="/nspireai")
    parser.add_argument("--n-link-timeout", type=float, default=15.0)
    parser.add_argument("--backend", choices=("echo", "openai"), default="echo")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-timeout", type=float, default=float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "45")))
    parser.add_argument("--poll-seconds", type=float, default=0.50)
    parser.add_argument("--once", action="store_true", help="process at most one request and exit")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        transport = make_transport(args)
        backend = make_backend(args.backend, args.model, os.environ.get("OPENAI_API_KEY"), args.base_url, args.api_timeout)
    except (RuntimeError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bridge = Bridge(transport, backend)
    print(f"bridge ready: transport={args.transport} backend={args.backend}", flush=True)
    while True:
        try:
            processed = bridge.step()
        except Exception as exc:
            print(f"bridge transport error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(args.poll_seconds)
            continue
        if processed:
            print(f"processed request {processed}", flush=True)
            if args.once:
                return 0
        if args.once:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
