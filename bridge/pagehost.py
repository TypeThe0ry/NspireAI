"""Host side of the thin-terminal page protocol (protocol 2).

The calculator page (src/page/page.c) only shows images and edits one input
line.  This module owns everything else: sessions, quick commands, menus and
rendering answers into image blocks.
"""
from __future__ import annotations

import itertools
import logging
import os
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import re

from .protocol import (
    OP_BLOCK,
    OP_CLEAR,
    OP_ERROR,
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
PREVIEW_MAX_H = 72          # PREVIEW_MAX_H in src/page/page.c

SYSTEM_PROMPT = (
    "You are an assistant used from a TI-Nspire calculator with a small "
    "320x240 screen. Be concise: answer first, then the key steps.\n"
    "Formatting that the screen can render: Markdown paragraphs, headings, "
    "lists, bold, inline code and code blocks, and LaTeX math written as "
    "$...$ (inline) or $$...$$ (display). Prefer short display formulas; "
    "avoid tables wider than three columns and avoid images.\n"
    "The keyboard has no Chinese input: the user may type Chinese as pinyin "
    "(with or without spaces or tones). Treat pinyin as Chinese, and answer "
    "in Chinese when the user writes Chinese or pinyin; otherwise answer in "
    "the user's language."
)

HELP_PAGES = [
    """\
## Keys
- **enter** send · **del** erase · **esc** close
- **arrows** scroll the chat / move the cursor
- **menu** quick commands · **cat** chats
- **var** thinking effort: off, low, high, max
- **doc** keyboard: qwerty + legend, qwerty, abc
- Chinese: type pinyin; the answer is Chinese

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


def parse_tags(text: str) -> tuple[dict[str, str], str]:
    """Split leading "#key:value " tags (think, cmd) from a request."""
    tags: dict[str, str] = {}
    while text.startswith("#"):
        head, sep, rest = text.partition(" ")
        key, colon, value = head[1:].partition(":")
        if not colon or not key.isalpha() or key not in ("think", "cmd"):
            break
        tags[key] = value
        text = rest if sep else ""
    return tags, text


_STRONG_MATH = re.compile(r"[\\^_{}]|[A-Za-z0-9)\]][=<>+*/][A-Za-z0-9(\\\[-]|[A-Za-z]\(")
_WEAK_MATH = re.compile(r"[-+]?\d+(\.\d+)?[A-Za-z]{0,2}|[A-Za-z]|[-+=<>*/()\[\]|]+")
_TRAILING = ".,;:?!"


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
    parts = re.split(r"(\s+)", text)
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
        self.menu_version = self._menu_version()

    # ----- helpers -------------------------------------------------------

    def _menu_version(self) -> int:
        digest = zlib.crc32(repr(self.commands.to_dict()).encode("utf-8"))
        digest = zlib.crc32(HELP_TEXT.encode("utf-8"), digest)
        return digest % 9000 + 1

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
        categories = self.commands.categories[:7]
        root: list[tuple[str, str, int, bytes]] = []
        for index, category in enumerate(categories):
            root.append((str(index + 1), category.display, ACT_GOTO,
                         bytes([SCREEN_CATEGORY_BASE + 10 * index])))
        root.append(("8", "Chats 会话", ACT_SEND_STAY, b"session.list"))
        root.append(("9", "Help 帮助", ACT_GOTO, bytes([SCREEN_HELP])))
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
        items.append(("9", "New chat 新会话", ACT_SEND, b"session.new"))
        items.append(("0", "Delete current 删除当前", ACT_SEND_STAY,
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
            active = self.store.active()
            self.send_state()
            if self.page.menu_version != self.menu_version:
                self.send_screens()
            # The page's own greeting lines count as blocks, so compare the
            # session instead of the block count alone.
            if self.page.session != active.id or self.page.blocks <= 3:
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
        try:
            answer = self._complete(messages, effort)
        except Exception as exc:
            log.exception("backend failed")
            reason = f"{type(exc).__name__}: {exc}"
            self._send(OP_ERROR, reason.encode("ascii", "replace")[:200], request_id)
            return
        with self.lock:
            self.store.append(session.id, "assistant", answer)
            self.send_image(KIND_AI, self.render.render_markdown(answer, self.cfg), request_id)
            self._send(OP_RESPONSE, b"", request_id)
            self.send_state()  # the first message sets the session title

    def _context(self, session_id: int, command: Optional[str], text: str) -> list[dict]:
        from .sessions import context_messages

        session = self.store.get(session_id)
        messages = context_messages(session)
        if command and messages and messages[-1]["role"] == "user":
            # The stored message keeps what the user typed; the model gets
            # the expanded command prompt.
            messages[-1] = {"role": "user", "content": self.commands.expand(command, text)}
        return [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    def _complete(self, messages: list[dict], effort: Optional[str]) -> str:
        complete = getattr(self.backend, "complete", None)
        if complete is not None:
            return complete(messages, effort=effort)
        return self.backend.answer(messages[-1]["content"])
