"""Host side of the thin-terminal page protocol (protocol 2).

The calculator page (src/page/page.c) only shows images and edits one input
line.  This module owns everything else: sessions, quick commands, menus and
rendering answers into image blocks.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import re

from .bridge import SetupNeeded
from .protocol import (
    OP_BLOCK,
    OP_CLEAR,
    OP_ERROR,
    OP_IME,
    OP_PREVIEW,
    OP_RESPONSE,
    OP_SCREEN,
    OP_STATE,
)

log = logging.getLogger("nspireai.pagehost")

KIND_AI, KIND_USER, KIND_INFO = 0, 1, 2
ACT_CLOSE, ACT_GOTO, ACT_INSERT, ACT_COMMAND, ACT_SEND, ACT_SEND_STAY = range(6)

SCREEN_ROOT = 1
SCREEN_HELP = 3             # first help page; the second is SCREEN_HELP + 1
SCREEN_CATEGORY_BASE = 10   # + 10 * category index + page
SCREEN_SESSIONS = 200

# Image strips keep each message small, so an answer appears progressively
# and the page's receive buffer stays modest.
STRIP_ROWS = 120
MENU_WIDTH = 312
MENU_HEIGHT = 220
SESSIONS_PER_PAGE = 7
HISTORY_TURNS = 6           # messages replayed when the page (re)opens a session
HELLO_SETTLE_SECONDS = float(os.environ.get("NSPIREAI_HELLO_SETTLE", "1.0"))
PREVIEW_MAX_H = 72          # PREVIEW_MAX_H in src/page/page.c
IME_CANDIDATES = 60         # looked up per composition; shown nine to a page at most
IME_TEXT_BYTES = 47         # IME_TEXT_CAP - 1 in src/page/page.c
MAX_TOOL_NOTES = 8          # progress notes shown for one question

# The model gets no system prompt unless NSPIREAI_SYSTEM_PROMPT is set: the
# page renders the Markdown and LaTeX the model writes by default, and the
# web tools describe themselves.
SYSTEM_PROMPT = os.environ.get("NSPIREAI_SYSTEM_PROMPT", "").strip()

HELP_PAGES = [
    """\
## Keys
- **enter** send · **del** erase · **esc** close
- **arrows** scroll the chat / move the cursor
- **menu** quick commands · **cat** chats
- **var** thinking effort: off, low, high, max
- **doc** keyboard: qwerty + legend, qwerty, abc
- **ctrl+space** or **menu 6** Chinese: pinyin, **1-9** or
  **space** pick, arrows = more, enter = letters
- **menu 7** web search on/off

**0** next page
""",
    """\
## Symbols: hold ctrl
- `(` `)` give `{` `}` · `7` `8` give `[` `]`
- `÷` gives `\\` · `−` gives `_` · `=` gives `$`
- `×` gives `&` · `+` gives `|` · `.` gives `;`
- `1` `!` · `2` `@` · `3` `#` · `0` `%` · `4` `5` `<` `>`

## Math keys
- `x²` types `^2`; with ctrl `\\sqrt{`
- fraction key types `\\frac{`; with ctrl `}{`
- `e^x` `10^x` `trig` type `e^` `10^` `\\sin(`
""",
]
HELP_TEXT = "\n".join(HELP_PAGES)  # part of the menu version


TAG_END = "\x1f"  # the page ends its tags with a unit separator


def parse_tags(text: str) -> tuple[dict[str, str], str]:
    """Split the page's "#think:<level> #cmd:<id> " tags from a request.

    The page ends its tags with TAG_END, which cannot be typed, so a question
    that itself begins with "#think:" is not mistaken for a tag.  Requests
    without TAG_END (older pages) are parsed leniently.
    """
    tags: dict[str, str] = {}
    head, separator, body = text.partition(TAG_END)
    if separator:
        for token in head.split():
            key, colon, value = token[1:].partition(":")
            if token.startswith("#") and colon and key in ("think", "cmd"):
                tags.setdefault(key, value)
        return tags, body
    while text.startswith("#"):
        head, sep, rest = text.partition(" ")
        key, colon, value = head[1:].partition(":")
        if not colon or key not in ("think", "cmd") or key in tags:
            break
        tags[key] = value
        text = rest if sep else ""
    return tags, text


_STRONG_MATH = re.compile(r"[\\^_{}]|[A-Za-z0-9)\]][=<>+*/][A-Za-z0-9(\\\[-]|[A-Za-z]\(")
_WEAK_MATH = re.compile(r"[-+]?\d+(\.\d+)?[A-Za-z]{0,2}|[A-Za-z]|[-+=<>*/()\[\]|]+")
_TRAILING = ".,;:?!"
_CJK_RUN = re.compile(r"([\u2e80-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]+)")


def close_groups(latex: str) -> str:
    """Make half-typed LaTeX renderable: close braces, fill empty groups."""
    depth = 0
    for index, char in enumerate(latex):
        if char == "\\":
            continue
        if index and latex[index - 1] == "\\":
            continue
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
    latex += "}" * depth
    latex = re.sub(r"\{\s*\}", r"{\\cdots}", latex)
    return re.sub(r"[\^_]$", "", latex)  # a dangling ^ or _ has nothing to raise yet


def auto_math(text: str) -> str:
    """Wrap the math-looking parts of plain input in $...$.

    The calculator user types `solve x^2 + \\frac{1}{2} = 0` without dollar
    signs; spans of math-like tokens become inline math so the preview and
    the chat history show them typeset.  Text that already uses $ or \\( is
    left alone.
    """
    if "$" in text or "\\(" in text or "\\[" in text:
        return text
    # Chinese is written without spaces: "求x^2的导数" is three parts.
    parts = [piece for part in re.split(r"(\s+)", text) for piece in _CJK_RUN.split(part)]
    out: list[str] = []
    span: list[str] = []

    def flush() -> None:
        while span and span[-1].isspace():
            out_tail.append(span.pop())
        body = "".join(span)
        strong = any(_STRONG_MATH.search(t) for t in span)
        operators = any(t in ("=", "+", "-", "*", "/", "<", ">") for t in span)
        operands = sum(1 for t in span if not t.isspace() and t not in "=+-*/<>")
        if body and (strong or (operators and operands >= 2)):
            tail = ""
            while body and body[-1] in _TRAILING:
                tail = body[-1] + tail
                body = body[:-1]
            out.append("$" + close_groups(body) + "$" + tail)
        else:
            out.append(body)
        out.extend(reversed(out_tail))
        out_tail.clear()
        span.clear()

    out_tail: list[str] = []
    for part in parts:
        if not part:
            continue
        core = part.rstrip(_TRAILING)
        mathy = bool(core) and (bool(_STRONG_MATH.search(core)) or bool(_WEAK_MATH.fullmatch(core)))
        if part.isspace():
            if span:
                span.append(part)
            else:
                out.append(part)
        elif mathy:
            span.append(part)
        else:
            flush()
            out.append(part)
    flush()
    return "".join(out)


def parse_kv(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in text.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def ascii_label(text: str, limit: int, fallback: str = "chat") -> str:
    kept = "".join(c for c in text if 32 <= ord(c) < 127 and c not in ";=").strip()
    kept = " ".join(kept.split())
    return (kept[:limit] or fallback) if len(kept) >= 2 else fallback


def png_from_rgb565_runs(payload: bytes) -> tuple[int, int, bytes]:
    """Decode an OP_DUMP payload into PNG bytes (no third-party imports)."""
    import struct

    if len(payload) < 5 or payload[4] != 1:
        raise ValueError("unsupported dump format")
    width, height = struct.unpack_from(">HH", payload)
    rows = bytearray()
    pixels = bytearray()
    for i in range(5, len(payload) - 2, 3):
        count = payload[i]
        v = (payload[i + 1] << 8) | payload[i + 2]
        r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
        rgb = bytes(((r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31))
        pixels += rgb * count
    if len(pixels) != width * height * 3:
        raise ValueError(f"dump has {len(pixels) // 3} pixels, expected {width * height}")
    for y in range(height):
        rows.append(0)
        rows += pixels[y * width * 3:(y + 1) * width * 3]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6))
    png += chunk(b"IEND", b"")
    return width, height, png


@dataclass
class PageInfo:
    conversation_id: int
    version: int = 0
    blocks: int = 0
    session: int = 0
    menu_version: int = 0
    max_packet: int = 0


class PageHost:
    """Drives one connected page.  `send(opcode, request_id, conversation_id,
    payload)` must fragment and transmit a logical message."""

    def __init__(self, send: Callable[[int, int, int, bytes], None], backend,
                 home: Optional[Path] = None):
        # Imported here so that the echo-only text bridge keeps working when
        # the rendering dependencies are not installed.
        from . import imagecodec, render
        from .commands import CommandSet
        from .sessions import SessionStore

        self.send = send
        self.backend = backend
        self.imagecodec = imagecodec
        self.render = render
        # Info notes are tinted gray by the page; render them in full ink.
        self.cfg = render.RenderConfig.from_env(max_height=3000, info_ink=0)
        self.home = Path(home) if home else Path(os.environ.get(
            "NSPIREAI_HOME", str(Path.home() / ".config" / "nspireai")))
        self.store = SessionStore(self.home)
        self.commands = CommandSet.load(
            Path(__file__).with_name("commands.default.json"),
            self.home / "commands.json")
        self.page: Optional[PageInfo] = None
        self.lock = threading.RLock()
        self._ids = itertools.count(1)
        self._screens: Optional[list[bytes]] = None
        self.ime = None                     # bridge/ime.py, loaded by load_ime()
        self._ime_chain = ("", "", "")      # letters, text, letters expected next
        self.web = self._load_web()         # bridge/webtools.py, None = unavailable
        self.settings = self._load_settings()
        self.menu_version = self._menu_version()

    # ----- helpers -------------------------------------------------------

    def _menu_version(self) -> int:
        digest = zlib.crc32(repr(self.commands.to_dict()).encode("utf-8"))
        digest = zlib.crc32(HELP_TEXT.encode("utf-8"), digest)
        digest = zlib.crc32(b"web" if self.web_enabled else b"", digest)
        return digest % 9000 + 1

    # ----- settings and internet access ------------------------------------

    def _load_settings(self) -> dict:
        try:
            raw = json.loads((self.home / "settings.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable settings.json: %s", exc)
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_settings(self) -> None:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            temporary = self.home / ".settings.json.tmp"
            temporary.write_text(json.dumps(self.settings, indent=1, sort_keys=True),
                                 encoding="utf-8")
            os.replace(temporary, self.home / "settings.json")
        except OSError as exc:
            log.warning("cannot save settings.json: %s", exc)

    def _load_web(self):
        """The web tools, or None when they are unavailable or turned off
        for good with NSPIREAI_WEB=0."""
        if os.environ.get("NSPIREAI_WEB", "1").strip().lower() in ("0", "off", "no", "false"):
            return None
        try:
            from .webtools import WebTools

            return WebTools.from_env()
        except Exception as exc:
            log.warning("internet access is unavailable: %s: %s", type(exc).__name__, exc)
            return None

    @property
    def web_enabled(self) -> bool:
        return (self.web is not None and getattr(self.backend, "supports_tools", False)
                and bool(getattr(self, "settings", {}).get("web", True)))

    def set_web(self, enabled: bool) -> None:
        self.settings["web"] = bool(enabled)
        self._save_settings()
        self._screens = None
        self.menu_version = self._menu_version()

    def _conversation(self) -> int:
        return self.page.conversation_id if self.page else 0

    def _send(self, opcode: int, payload: bytes, request_id: Optional[int] = None) -> None:
        self.send(opcode, request_id if request_id is not None else next(self._ids),
                  self._conversation(), payload)

    def send_state(self) -> None:
        session = self.store.active()
        title = ascii_label(session.title, 16)
        self._send(OP_STATE, f"s={session.id};t={title};mv={self.menu_version}".encode("ascii"))

    def send_image(self, kind: int, image, block_id: int) -> None:
        """Send an image as strips; strips after the first join seamlessly."""
        width, height = image.size
        for top in range(0, height, STRIP_ROWS):
            strip = image.crop((0, top, width, min(height, top + STRIP_ROWS)))
            payload = bytearray(self.imagecodec.encode_block(kind, strip, block_id, bpp=4))
            if top > 0:
                payload[7] |= 1  # continuation: no gap above
            self._send(OP_BLOCK, bytes(payload))

    def send_info(self, text: str) -> None:
        self.send_image(KIND_INFO, self.render.render_info(text, self.cfg), 0)

    def send_history(self) -> None:
        session = self.store.active()
        self._send(OP_CLEAR, b"")
        for message in session.messages[-HISTORY_TURNS:]:
            if message["role"] == "user":
                image = self.render.render_user_turn(self._user_display(message), self.cfg)
                self.send_image(KIND_USER, image, 0)
            else:
                self.send_image(KIND_AI, self.render.render_markdown(message["content"], self.cfg), 0)

    @staticmethod
    def _user_display(message: dict) -> str:
        cmd = message.get("cmd")
        shown = auto_math(message["content"])
        return f"[{cmd}] {shown}" if cmd else shown

    # ----- screens -------------------------------------------------------

    def _screen(self, screen_id: int, title: str, items: list[tuple[str, str, int, bytes]],
                footer: Optional[str], show: bool = False) -> bytes:
        """items: (key, label, action, arg)."""
        cfg = self.render.RenderConfig(**{**self.cfg.__dict__, "width": MENU_WIDTH})
        image = self.render.render_menu(title, [(k, label) for k, label, _, _ in items],
                                        cfg, footer=footer, height_limit=MENU_HEIGHT)
        keys = [self.imagecodec.ScreenKey(key=k, action=action, arg=arg)
                for k, _, action, arg in items]
        return self.imagecodec.encode_screen(screen_id, 1 if show else 0, keys, image, bpp=4)

    def build_screens(self) -> list[bytes]:
        screens: list[bytes] = []
        categories = self.commands.categories[:5]   # 6-9 are fixed entries
        root: list[tuple[str, str, int, bytes]] = []
        for index, category in enumerate(categories):
            root.append((str(index + 1), category.display, ACT_GOTO,
                         bytes([SCREEN_CATEGORY_BASE + 10 * index])))
        if self.web is not None and getattr(self.backend, "supports_tools", False):
            state = "on" if self.web_enabled else "off"
            root.append(("7", f"Web search: {state}", ACT_SEND, b"web.toggle"))
        # The page toggles Chinese input itself when it sees this argument.
        root.append(("6", "Chinese input on/off", ACT_INSERT, b"<ime>"))
        root.append(("8", "Chats", ACT_SEND_STAY, b"session.list"))
        root.append(("9", "Help", ACT_GOTO, bytes([SCREEN_HELP])))
        screens.append(self._screen(SCREEN_ROOT, "Menu", root, "number = choose · esc = close"))

        for index, category in enumerate(categories):
            pages = self.commands.pages(category.id, per_page=9)[:9]
            for page_number, page in enumerate(pages):
                items: list[tuple[str, str, int, bytes]] = []
                for slot, item in enumerate(page):
                    label = item.display
                    if item.type == "insert":
                        action, arg = ACT_INSERT, item.text.encode("ascii", "replace")
                    else:
                        action, arg = ACT_COMMAND, f"{item.id}|{item.label}".encode("ascii", "replace")
                    items.append((str(slot + 1), label, action, arg))
                base = SCREEN_CATEGORY_BASE + 10 * index
                if len(pages) > 1:
                    nxt = base + (page_number + 1) % len(pages)
                    items.append(("0", "more…", ACT_GOTO, bytes([nxt])))
                    footer = f"page {page_number + 1}/{len(pages)} · esc = close"
                else:
                    items.append(("0", "back", ACT_GOTO, bytes([SCREEN_ROOT])))
                    footer = "esc = close"
                screens.append(self._screen(base + page_number,
                                            category.display, items, footer))

        cfg = self.render.RenderConfig(**{**self.cfg.__dict__, "width": MENU_WIDTH,
                                          "max_height": MENU_HEIGHT, "font_size": 12})
        for index, text in enumerate(HELP_PAGES):
            image = self.render.render_markdown(text, cfg)
            if image.size[1] > MENU_HEIGHT:
                image = image.crop((0, 0, MENU_WIDTH, MENU_HEIGHT))
            nxt = SCREEN_HELP + index + 1 if index + 1 < len(HELP_PAGES) else SCREEN_ROOT
            keys = [self.imagecodec.ScreenKey(key="0", action=ACT_GOTO, arg=bytes([nxt]))]
            screens.append(self.imagecodec.encode_screen(SCREEN_HELP + index, 0, keys, image, bpp=4))
        return screens

    def send_screens(self) -> None:
        if self._screens is None:
            self._screens = self.build_screens()
        for payload in self._screens:
            self._send(OP_SCREEN, payload)

    def send_sessions_screen(self, page_number: int = 0) -> None:
        sessions = self.store.list()
        active = self.store.active()
        pages = max(1, (len(sessions) + SESSIONS_PER_PAGE - 1) // SESSIONS_PER_PAGE)
        page_number %= pages
        shown = sessions[page_number * SESSIONS_PER_PAGE:(page_number + 1) * SESSIONS_PER_PAGE]
        items: list[tuple[str, str, int, bytes]] = []
        for slot, session in enumerate(shown):
            mark = "● " if session.id == active.id else ""
            items.append((str(slot + 1), f"{mark}{session.title}", ACT_SEND,
                          f"session.select {session.id}".encode("ascii")))
        if pages > 1:
            items.append(("8", "more…", ACT_SEND_STAY,
                          f"session.list {page_number + 1}".encode("ascii")))
        items.append(("9", "New chat", ACT_SEND, b"session.new"))
        items.append(("0", "Delete current", ACT_SEND_STAY,
                      f"session.delete {active.id}".encode("ascii")))
        footer = f"page {page_number + 1}/{pages} · esc = close"
        self._send(OP_SCREEN, self._screen(SCREEN_SESSIONS, "Chats", items, footer, show=True))

    # ----- page events ---------------------------------------------------

    def on_hello(self, conversation_id: int, payload: bytes) -> None:
        fields = parse_kv(payload.decode("ascii", "replace"))

        def number(key: str) -> int:
            try:
                return int(fields.get(key, "0"))
            except ValueError:
                return 0

        with self.lock:
            self.page = PageInfo(conversation_id, number("v"), number("n"),
                                 number("sid"), number("mv"), number("max"))
            # Always send everything.  The page notes the menu version and
            # the session as soon as STATE arrives, so after a link that
            # dropped half-way its HELLO would claim data it never received.
            self.send_state()
        # Give a page that has just opened a moment before the bulk of it.
        time.sleep(HELLO_SETTLE_SECONDS)
        with self.lock:
            self.send_screens()
            self.send_history()

    def on_action(self, payload: bytes) -> None:
        action = payload.decode("ascii", "replace").strip()
        name, _, arg = action.partition(" ")
        with self.lock:
            try:
                if name == "session.list":
                    self.send_sessions_screen(int(arg) if arg.isdigit() else 0)
                elif name == "session.new":
                    self.store.create()
                    self.send_state()
                    self.send_history()
                elif name == "session.select" and arg.isdigit():
                    self.store.select(int(arg))
                    self.send_state()
                    self.send_history()
                elif name == "session.delete" and arg.isdigit():
                    before = self.store.active().id
                    self.store.delete(int(arg))
                    if self.store.active().id != before:
                        self.send_state()
                        self.send_history()
                    self.send_sessions_screen(0)
                elif name == "web.toggle" and self.web is not None \
                        and getattr(self.backend, "supports_tools", False):
                    self.set_web(not self.web_enabled)
                    self.send_state()
                    self.send_screens()
                    self.send_info("Web search is on." if self.web_enabled
                                   else "Web search is off.")
                else:
                    log.warning("unknown page action %r", action)
            except Exception as exc:  # never let a menu action kill the reader
                log.exception("page action %r failed", action)
                self.send_info(f"action failed: {type(exc).__name__}")

    def on_preview(self, request_id: int, payload: bytes) -> None:
        """Typeset the input line while the user types."""
        text = payload.decode("utf-8", "replace")
        typeset = auto_math(text)
        if typeset == text and text.isascii():
            self._send(OP_PREVIEW, b"", request_id)  # the raw line says it all
            return
        image = self.render.render_preview(typeset, self.cfg) if hasattr(
            self.render, "render_preview") else self.render.render_markdown(typeset, self.cfg)
        width, height = image.size
        if height > PREVIEW_MAX_H:  # keep the end, where the user is typing
            image = image.crop((0, height - PREVIEW_MAX_H, width, height))
        payload = self.imagecodec.encode_block(KIND_USER, image, request_id & 0xFFFFFFFF, bpp=4)
        self._send(OP_PREVIEW, payload, request_id)

    def on_request(self, request_id: int, payload: bytes) -> None:
        """Answer one question.  Runs on the bridge's worker thread."""
        tags, text = parse_tags(payload.decode("utf-8", "replace"))
        text = text.strip()
        effort = tags.get("think")
        command = tags.get("cmd") or None
        with self.lock:
            session = self.store.active()
            label = self.commands.label(command) if command else None
            self.store.append(session.id, "user", text, think=effort, cmd=label)
            shown = auto_math(text)
            if label:
                shown = f"[{label}] {shown}"
            self.send_image(KIND_USER, self.render.render_user_turn(shown, self.cfg), request_id)
            messages = self._context(session.id, command, text)
            tools = self.web.session(text) if self.web_enabled else None
        notes = itertools.count()

        def progress(note: str) -> None:
            """Show what the model is looking up while the user waits."""
            if next(notes) >= MAX_TOOL_NOTES:
                return
            with self.lock:
                if self.store.active().id == session.id:
                    self.send_info(note)

        try:
            answer = self._complete(messages, effort, tools, progress)
        except SetupNeeded as exc:
            # Shown in full, but not part of the conversation.
            with self.lock:
                self.send_image(KIND_INFO, self.render.render_markdown(str(exc), self.cfg), request_id)
                self._send(OP_RESPONSE, b"", request_id)
            return
        except Exception as exc:
            log.exception("backend failed")
            reason = f"{type(exc).__name__}: {exc}"
            self._send(OP_ERROR, reason.encode("ascii", "replace")[:200], request_id)
            return
        with self.lock:
            try:
                current = self.store.get(session.id)
            except KeyError:
                current = None
            if current is None or current.created != session.created:
                # Deleted while the model was thinking; the same number may
                # already belong to a new chat, which must not get the answer.
                self.send_info("That chat was deleted; its answer was dropped.")
            else:
                self.store.append(session.id, "assistant", answer)
                if self.store.active().id == session.id:
                    self.send_image(KIND_AI, self.render.render_markdown(answer, self.cfg), request_id)
                else:
                    # The user switched chats while the model was thinking.
                    self.send_info(f"The answer was saved in chat {session.id}.")
            self._send(OP_RESPONSE, b"", request_id)
            if self._menu_version() != self.menu_version:
                # The backend became ready (an API key appeared): the menu
                # gains its web switch.
                self.menu_version = self._menu_version()
                self._screens = None
                self.send_screens()
            self.send_state()  # the first message sets the session title

    def _context(self, session_id: int, command: Optional[str], text: str) -> list[dict]:
        from .sessions import context_messages

        session = self.store.get(session_id)
        messages = context_messages(session)
        if command and messages and messages[-1]["role"] == "user":
            # The stored message keeps what the user typed; the model gets
            # the expanded command prompt.
            messages[-1] = {"role": "user", "content": self.commands.expand(command, text)}
        if SYSTEM_PROMPT:
            return [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        return messages

    def _complete(self, messages: list[dict], effort: Optional[str], tools=None,
                  progress: Optional[Callable[[str], None]] = None) -> str:
        complete = getattr(self.backend, "complete", None)
        if complete is None:
            return self.backend.answer(messages[-1]["content"])
        if tools is not None:
            return complete(messages, effort=effort, tools=tools, progress=progress)
        return complete(messages, effort=effort)

    # ----- pinyin input ---------------------------------------------------

    def load_ime(self) -> None:
        """Load the dictionary (seconds the first time); call from a thread."""
        try:
            from .ime import PinyinIME

            engine = PinyinIME(user_dir=self.home)
            engine.load()
            self.ime = engine
        except Exception as exc:
            log.warning("pinyin input is unavailable: %s: %s", type(exc).__name__, exc)

    def on_ime(self, request_id: int, payload: bytes) -> None:
        """Candidates for the letters being composed, one page of them."""
        if not payload:
            return
        wanted = payload[0]
        letters = payload[1:].decode("ascii", "ignore")
        engine = self.ime
        ready = engine is not None and getattr(engine, "ready", False)
        found = engine.candidates(letters, IME_CANDIDATES) if ready else []
        found = [item for item in found
                 if 0 < len(item.text.encode("utf-8")) <= IME_TEXT_BYTES and 0 < item.consumed <= 255]
        pages = self.render.layout_candidates([item.text for item in found], self.cfg)
        if not pages:
            note = "no match: enter keeps the letters" if ready else "loading the dictionary..."
            image = self.render.render_candidates([], self.cfg, note=note)
            body = bytes([0, 0, 0, 0])
        else:
            number = min(wanted, len(pages) - 1)
            first, count = pages[number]
            shown = found[first:first + count]
            flags = (1 if number > 0 else 0) | (2 if number + 1 < len(pages) else 0)
            image = self.render.render_candidates(
                [item.text for item in shown], self.cfg,
                previous=bool(flags & 1), following=bool(flags & 2))
            body = bytes([len(shown), flags, number, 0])
            for item in shown:
                text = item.text.encode("utf-8")
                body += bytes([item.consumed, len(text)]) + text
        body += self.imagecodec.encode_block(KIND_AI, image, request_id & 0xFFFFFFFF, bpp=2)
        self._send(OP_IME, body, request_id)

    def on_ime_pick(self, payload: bytes) -> None:
        """Learn from a choice: "letters<TAB>text<TAB>letters still composed"."""
        engine = self.ime
        if engine is None or not getattr(engine, "ready", False):
            return
        fields = payload.decode("utf-8", "replace").split("\t")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            return
        letters, text = fields[0], fields[1]
        rest = fields[2] if len(fields) > 2 else ""
        engine.learn(letters, text)
        # A phrase entered piece by piece ("jie" 解, "fangcheng" 方程) is
        # learned as a whole, so that it is one pick the next time.
        so_far, phrase, expected = self._ime_chain
        if not so_far or not (letters + rest).startswith(expected):
            so_far, phrase = "", ""
        so_far, phrase = so_far + letters, phrase + text
        if rest:
            self._ime_chain = (so_far, phrase, rest)
            return
        if so_far != letters:
            engine.learn(so_far, phrase)
        self._ime_chain = ("", "", "")
