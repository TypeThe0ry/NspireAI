from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from bridge import imagecodec
from bridge.bridge import EchoBackend
from bridge.pagehost import (
    PREVIEW_MAX_H,
    SCREEN_ROOT,
    PageHost,
    ascii_label,
    auto_math,
    close_groups,
    parse_kv,
    parse_tags,
    png_from_rgb565_runs,
)
from bridge.protocol import (
    OP_BLOCK,
    OP_CLEAR,
    OP_PREVIEW,
    OP_RESPONSE,
    OP_SCREEN,
    OP_STATE,
)


class HelperTests(unittest.TestCase):
    def test_parse_tags(self):
        self.assertEqual(parse_tags("#think:high #cmd:solve x^2=4"),
                         ({"think": "high", "cmd": "solve"}, "x^2=4"))
        self.assertEqual(parse_tags("plain text"), ({}, "plain text"))
        # Only known tags are stripped; a hashtag in the question stays.
        self.assertEqual(parse_tags("#hello world"), ({}, "#hello world"))
        self.assertEqual(parse_tags("#think:off"), ({"think": "off"}, ""))

    def test_parse_kv_and_label(self):
        self.assertEqual(parse_kv("v=2;w=320;n=0"), {"v": "2", "w": "320", "n": "0"})
        self.assertEqual(ascii_label("解方程 x^2", 16), "x^2")
        self.assertEqual(ascii_label("勾股定理", 16), "chat")
        self.assertEqual(ascii_label("a;b=c", 16), "abc")

    def test_auto_math(self):
        cases = {
            "solve x^2 + \\frac{1}{2} = 0 for x": "solve $x^2 + \\frac{1}{2} = 0$ for x",
            "what is 12*17": "what is $12*17$",
            "hello world": "hello world",
            "I have 3 apples": "I have 3 apples",
            "jie fang cheng x^2=4.": "jie fang cheng $x^2=4$.",
            "f(x) = 2x + 1, find f(3)": "$f(x) = 2x + 1$, find $f(3)$",
            "already $x^2$ here": "already $x^2$ here",
        }
        for text, expected in cases.items():
            self.assertEqual(auto_math(text), expected, text)

    def test_close_groups(self):
        self.assertEqual(close_groups("\\frac{1}{"), "\\frac{1}{\\cdots}")
        self.assertEqual(close_groups("x^"), "x")
        self.assertEqual(close_groups("\\sqrt{x+1"), "\\sqrt{x+1}")

    def test_dump_decoding(self):
        payload = struct.pack(">HHB", 2, 2, 1)
        payload += bytes([3, 0xF8, 0x00]) + bytes([1, 0x00, 0x1F])  # 3 red, 1 blue
        width, height, png = png_from_rgb565_runs(payload)
        self.assertEqual((width, height), (2, 2))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(ValueError):
            png_from_rgb565_runs(struct.pack(">HHB", 2, 2, 1) + bytes([3, 0, 0]))


class PageHostTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[tuple[int, int, int, bytes]] = []
        self.home = tempfile.TemporaryDirectory()
        self.host = PageHost(lambda *frame: self.sent.append(frame), EchoBackend(),
                             home=Path(self.home.name))

    def tearDown(self):
        self.home.cleanup()

    def opcodes(self) -> list[int]:
        return [frame[0] for frame in self.sent]

    def test_hello_sends_state_screens_and_history(self):
        self.host.on_hello(7, b"v=2;w=320;h=240;bpp=4;max=224;n=0;sid=0;mv=0")
        ops = self.opcodes()
        self.assertEqual(ops[0], OP_STATE)
        self.assertIn(OP_SCREEN, ops)
        self.assertIn(OP_CLEAR, ops)
        self.assertTrue(all(frame[2] == 7 for frame in self.sent))
        screens = [imagecodec.decode_screen(f[3]) for f in self.sent if f[0] == OP_SCREEN]
        self.assertIn(SCREEN_ROOT, [screen[0] for screen in screens])
        for screen in screens:
            self.assertLessEqual(screen[5], 222)

        # A page that already has the menu and the session gets neither again.
        state = self.sent[0][3].decode()
        fields = parse_kv(state)
        self.sent.clear()
        hello = f"v=2;n=9;sid={fields['s']};mv={fields['mv']}".encode()
        self.host.on_hello(7, hello)
        self.assertEqual(self.opcodes(), [OP_STATE])

    def test_request_renders_user_turn_answer_and_completes(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        self.sent.clear()
        self.host.on_request(42, b"#think:low #cmd:solve x^2=4")
        ops = self.opcodes()
        self.assertEqual(ops[0], OP_BLOCK)
        self.assertEqual(ops[-2:], [OP_RESPONSE, OP_STATE])
        user_block = imagecodec.decode_block(self.sent[0][3])
        self.assertEqual(user_block[0], imagecodec.KIND_USER)
        self.assertEqual(user_block[4], 42)  # replaces the page's local echo
        response = next(f for f in self.sent if f[0] == OP_RESPONSE)
        self.assertEqual((response[1], response[3]), (42, b""))
        session = self.host.store.active()
        self.assertEqual([m["role"] for m in session.messages], ["user", "assistant"])
        self.assertEqual(session.messages[0]["content"], "x^2=4")  # tags stripped

    def test_context_is_kept_per_session(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        self.host.on_request(1, b"first question")
        self.host.on_request(2, b"second question")
        first = self.host.store.active()
        self.assertIn("turn 2", first.messages[-1]["content"])
        self.host.on_action(b"session.new")
        self.host.on_request(3, b"new chat question")
        self.assertIn("turn 1", self.host.store.active().messages[-1]["content"])
        self.host.on_action(f"session.select {first.id}".encode())
        self.assertEqual(self.host.store.active().id, first.id)

    def test_preview(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        self.sent.clear()
        self.host.on_preview(5, b"hello")
        self.assertEqual(self.sent[-1][0], OP_PREVIEW)
        self.assertEqual(self.sent[-1][3], b"")  # nothing to typeset
        self.host.on_preview(6, b"x^2 + \\frac{1}{")
        frame = self.sent[-1]
        self.assertEqual((frame[0], frame[1]), (OP_PREVIEW, 6))
        block = imagecodec.decode_block(frame[3])
        self.assertLessEqual(block[3], PREVIEW_MAX_H)

    def test_sessions_screen_and_delete(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        self.host.on_action(b"session.new")
        self.sent.clear()
        self.host.on_action(b"session.list")
        screen = imagecodec.decode_screen(self.sent[-1][3])
        self.assertEqual(screen[1] & 1, 1)  # shown immediately
        keys = {key.key for key in screen[2]}
        self.assertTrue({"1", "2", "9", "0"} <= keys)
        active = self.host.store.active().id
        self.host.on_action(f"session.delete {active}".encode())
        self.assertNotEqual(self.host.store.active().id, active)

    def test_unknown_action_does_not_raise(self):
        self.host.on_action(b"no.such.action 1")


if __name__ == "__main__":
    unittest.main()
