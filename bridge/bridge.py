from __future__ import annotations

import argparse
import json
import os
import re
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


ECHO_DEMO = """\
## 二次方程 demo
方程 $ax^2+bx+c=0$ 的解为

$$x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$$

- 判别式 $\\Delta=b^2-4ac$
- **积分** $\\int_0^1 x^2\\,dx=\\frac{1}{3}$，求和 $\\sum_{k=1}^{n}k=\\frac{n(n+1)}{2}$

English text wraps too, and `inline code` stays monospaced.
"""


MAX_TOOL_ROUNDS = 5     # requests that may call tools, for one question


class EchoBackend:
    supports_tools = True   # "search <words>" and "open <url>" exercise the web tools

    def answer(self, prompt: str) -> str:
        return f"Mac received: {prompt}"

    def complete(self, messages: list[dict], effort: Optional[str] = None, tools=None,
                 progress=None) -> str:
        """Stateless form used by the page host; "demo" returns rich sample text."""
        last = messages[-1]["content"] if messages else ""
        if last.strip().lower() == "demo":
            return ECHO_DEMO
        verb, _, rest = last.strip().partition(" ")
        call = {"search": ("web_search", "query"), "open": ("open_url", "url")}.get(verb.lower())
        if tools is not None and call and rest.strip():
            arguments = json.dumps({call[1]: rest.strip()})
            if progress is not None:
                progress(tools.describe(call[0], arguments))
            return "```\n" + tools.call(call[0], arguments) + "\n```"
        turns = sum(1 for m in messages if m["role"] == "user")
        return f"Echo (turn {turns}, think {effort or 'default'}): {last}"

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


THINK_PREFIX = "#think:"


def split_think_prefix(prompt: str) -> tuple[Optional[str], str]:
    """The page prefixes requests with "#think:<off|low|high|max> "."""
    if prompt.startswith(THINK_PREFIX):
        head, _, rest = prompt[len(THINK_PREFIX):].partition(" ")
        if head in ("off", "low", "high", "max"):
            return head, rest
    return None, prompt


# The calculator page shows 53 columns and keeps about 3 KB of an answer, so
# the model is asked for short plain-text replies.
CALCULATOR_SYSTEM_PROMPT = (
    "You are answering on a TI-Nspire CX II calculator screen: 53 characters "
    "wide, plain ASCII only, no Markdown, no tables, no code fences. Keep "
    "answers short (a few sentences); use short lines."
)


class ChatCompletionsBackend:
    """OpenAI-compatible chat.completions backend (DeepSeek and others)."""

    supports_effort = True
    supports_tools = True

    def __init__(self, model: str, api_key: Optional[str], base_url: Optional[str], timeout: float = 45.0):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "chat backend requires the OpenAI SDK: python3 -m pip install -r bridge/requirements.txt"
            ) from exc
        kwargs = {"timeout": timeout}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.messages: list[dict[str, str]] = []

    def reset(self) -> None:
        self.messages.clear()

    @staticmethod
    def _effort_kwargs(effort: Optional[str]) -> dict:
        if effort == "off":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        if effort in ("low", "high", "max"):
            return {"reasoning_effort": effort,
                    "extra_body": {"thinking": {"type": "enabled"}}}
        return {}

    def complete(self, messages: list[dict], effort: Optional[str] = None, tools=None,
                 progress=None) -> str:
        """Stateless completion: the caller (page host) owns the history.

        `tools` is a session of bridge/webtools.py: the model may search the
        web and read pages before it answers; `progress(note)` is told what
        it looks up.
        """
        options = dict(
            model=self.model,
            max_tokens=int(os.environ.get("NSPIREAI_MAX_TOKENS", "1500")),
            **self._effort_kwargs(effort),
        )
        if tools is None:
            response = self.client.chat.completions.create(messages=messages, **options)
            return (response.choices[0].message.content or "").strip()

        from .webtools import tool_specs

        specs = tool_specs()
        conversation = list(messages)
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            last = round_number == MAX_TOOL_ROUNDS
            response = self.client.chat.completions.create(
                messages=conversation, tools=specs,
                tool_choice="none" if last else "auto", **options)
            message = response.choices[0].message
            calls = [] if last else list(getattr(message, "tool_calls", None) or [])
            if not calls:
                return (message.content or "").strip()
            conversation.append(self._assistant_turn(message, calls))
            for call in calls:
                name, arguments = call.function.name, call.function.arguments or "{}"
                if progress is not None:
                    try:
                        progress(tools.describe(name, arguments))
                    except Exception:
                        pass    # a note that cannot be shown must not cost the answer
                conversation.append({"role": "tool", "tool_call_id": call.id,
                                     "content": tools.call(name, arguments)})
        return ""

    @staticmethod
    def _assistant_turn(message, calls) -> dict:
        """The model's tool request as it has to be sent back."""
        turn = {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [{"id": call.id, "type": "function",
                            "function": {"name": call.function.name,
                                         "arguments": call.function.arguments or "{}"}}
                           for call in calls],
        }
        # DeepSeek's thinking mode rejects a tool round whose reasoning is
        # not returned with it.
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is not None:
            turn["reasoning_content"] = reasoning
        return turn

    def answer(self, prompt: str, effort: Optional[str] = None) -> str:
        """`effort` is off/low/high/max from the calculator page (None = model default)."""
        self.messages.append({"role": "user", "content": prompt})
        kwargs = self._effort_kwargs(effort)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": CALCULATOR_SYSTEM_PROMPT}] + self.messages,
                max_tokens=600,
                **kwargs,
            )
            answer = (response.choices[0].message.content or "").strip()
        except Exception:
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


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"


class SetupNeeded(RuntimeError):
    """The backend cannot answer until the user fixes the configuration;
    the message (Markdown) says how.  It is shown, never stored as an answer."""


ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, parsed like scripts/run-navnet-bridge.sh does: never
    executed; quoted values keep their spaces, other values must have none."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        match = ENV_LINE.match(line)
        if not match or line.startswith("#"):
            continue
        key, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif any(char.isspace() for char in value):
            continue
        values[key] = value
    return values


def env_file_path() -> Path:
    return Path(os.environ.get("NSPIREAI_ENV", str(Path.home() / ".config" / "nspireai" / "env")))


class SetupNeededBackend:
    """Stands in while the backend cannot be built (a missing API key).

    Every question first re-reads the env file; once it holds what was
    missing, the real backend is built and answers from then on.  Until
    then the calculator is told what is missing (SetupNeeded), instead of
    waiting for a bridge that never started."""

    def __init__(self, message: str, key: str = "", build=None):
        self.message = message
        self.key = key
        self.build = build
        self.real = None
        print(f"backend not configured: {message}", file=sys.stderr, flush=True)

    @property
    def supports_tools(self) -> bool:
        return getattr(self.real, "supports_tools", False)

    def _ready(self):
        if self.real is None and self.key and self.build is not None:
            value = read_env_file(env_file_path()).get(self.key, "").strip()
            if value:
                os.environ[self.key] = value
                self.real = self.build()
                print(f"{self.key} found; backend ready", file=sys.stderr, flush=True)
        return self.real

    def answer(self, prompt: str) -> str:
        real = self._ready()
        return real.answer(prompt) if real is not None else self.message

    def complete(self, messages: list[dict], effort: Optional[str] = None, tools=None,
                 progress=None) -> str:
        real = self._ready()
        if real is None:
            raise SetupNeeded(self.message)
        if tools is not None:
            return real.complete(messages, effort=effort, tools=tools, progress=progress)
        return real.complete(messages, effort=effort)

    def reset(self) -> None:
        if self.real is not None:
            self.real.reset()


DEEPSEEK_KEY_MISSING = (
    "**DeepSeek is not set up on the computer.** `DEEPSEEK_API_KEY` is missing "
    "from `~/.config/nspireai/env` (or that line is malformed). Run this in a "
    "terminal, then ask again (no restart needed):\n\n"
    "```\nprintf 'DEEPSEEK_API_KEY=%s\\n' 'YOUR-KEY' > ~/.config/nspireai/env\n```"
)


def make_backend(name: str, model: str, api_key: Optional[str], base_url: Optional[str], timeout: float):
    if name == "echo":
        return EchoBackend()
    if name == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return SetupNeededBackend(DEEPSEEK_KEY_MISSING, "DEEPSEEK_API_KEY",
                                  lambda: make_backend(name, model, api_key, base_url, timeout))
    if name == "deepseek":
        return ChatCompletionsBackend(
            model=os.environ.get("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
            timeout=timeout,
        )
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
    parser.add_argument("--backend", choices=("echo", "openai", "deepseek"), default="echo")
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
