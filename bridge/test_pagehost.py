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
    OP_IME,
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
        # With the page's separator, the user's own text is never parsed.
        self.assertEqual(parse_tags("#think:low \x1f#cmd:not a tag"),
                         ({"think": "low"}, "#cmd:not a tag"))
        self.assertEqual(parse_tags("#think:high #cmd:solve \x1fx^2=4"),
                         ({"think": "high", "cmd": "solve"}, "x^2=4"))

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
            # Chinese has no spaces: the formula is cut out of the sentence.
            "求x^2+1的导数": "求$x^2+1$的导数",
            "f(x)=2x+1，求f(3)": "$f(x)=2x+1$，求$f(3)$",
            "因式分解x^2-4。": "因式分解$x^2-4$。",
            "3个苹果": "3个苹果",
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
        self.host.web = None    # these tests never reach the network

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

        # A reconnecting page gets everything again: it may have noted the
        # menu version and session from a transfer that never completed.
        state = self.sent[0][3].decode()
        fields = parse_kv(state)
        self.sent.clear()
        hello = f"v=2;n=9;sid={fields['s']};mv={fields['mv']}".encode()
        self.host.on_hello(7, hello)
        self.assertIn(OP_SCREEN, self.opcodes())
        self.assertIn(OP_CLEAR, self.opcodes())

    def test_answer_for_a_chat_the_user_left_is_not_shown(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        first = self.host.store.active()

        class Switching:
            def complete(inner, messages, effort=None):
                self.host.on_action(b"session.new")  # the user switches meanwhile
                return "late answer"

        self.host.backend = Switching()
        self.sent.clear()
        self.host.on_request(9, b"#think:off \x1fquestion")
        self.assertEqual(self.host.store.get(first.id).messages[-1]["content"], "late answer")
        blocks = [imagecodec.decode_block(f[3]) for f in self.sent if f[0] == OP_BLOCK]
        self.assertNotIn(imagecodec.KIND_ASSISTANT, [b[0] for b in blocks[1:]])
        self.assertIn(OP_RESPONSE, self.opcodes())

    def test_answer_for_a_deleted_chat_is_dropped(self):
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        first = self.host.store.active()

        class Deleting:
            def complete(inner, messages, effort=None):
                # The user deletes the chat meanwhile; the fresh chat that the
                # store creates gets the same number.
                self.host.on_action(f"session.delete {first.id}".encode())
                self.assertEqual(self.host.store.active().id, first.id)
                return "late answer"

        self.host.backend = Deleting()
        self.sent.clear()
        self.host.on_request(9, b"#think:off \x1fquestion")
        self.assertEqual(self.host.store.active().messages, [])
        blocks = [imagecodec.decode_block(f[3])[0] for f in self.sent if f[0] == OP_BLOCK]
        self.assertNotIn(imagecodec.KIND_ASSISTANT, blocks)
        self.assertIn(OP_RESPONSE, self.opcodes())

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


class FakeIME:
    ready = True

    def __init__(self, found):
        self.found = found
        self.learned: list[tuple[str, str]] = []

    def candidates(self, letters, limit=60):
        return list(self.found)[:limit]

    def learn(self, letters, text):
        self.learned.append((letters, text))


def parse_ime(payload: bytes):
    count, flags, page = payload[0], payload[1], payload[2]
    position, found = 4, []
    for _ in range(count):
        consumed, length = payload[position], payload[position + 1]
        found.append((payload[position + 2:position + 2 + length].decode("utf-8"), consumed))
        position += 2 + length
    return found, flags, page, imagecodec.decode_block(payload[position:])


class InputMethodTests(unittest.TestCase):
    def setUp(self):
        from bridge.ime import Candidate

        self.sent: list[tuple[int, int, int, bytes]] = []
        self.home = tempfile.TemporaryDirectory()
        self.host = PageHost(lambda *frame: self.sent.append(frame), EchoBackend(),
                             home=Path(self.home.name))
        self.host.web = None
        words = ["这个方程怎么解", "这个", "这歌"] + list("这着者折哲浙遮褶蔗辙")
        self.host.ime = FakeIME([Candidate(word, 22 if len(word) > 2 else 2 + len(word))
                                 for word in words])

    def tearDown(self):
        self.home.cleanup()

    def ask(self, page: int, letters: str, request: int = 5):
        self.sent.clear()
        self.host.on_ime(request, bytes([page]) + letters.encode("ascii"))
        self.assertEqual([frame[0] for frame in self.sent], [OP_IME])
        self.assertEqual(self.sent[0][1], request)  # the page matches it to its revision
        return parse_ime(self.sent[0][3])

    def test_candidates_are_paged_to_fit_the_bar(self):
        first, flags, page, image = self.ask(0, "zhegefangchengzenmejie")
        self.assertEqual(first[0], ("这个方程怎么解", 22))
        self.assertEqual((flags, page), (2, 0))  # more pages follow
        self.assertTrue(1 < len(first) <= 9)
        _kind, bpp, width, height, _block, _picture = image
        self.assertEqual((bpp, width), (2, 320))
        self.assertLessEqual(height, 40)   # the page refuses a taller bar

        second, flags, page, _image = self.ask(1, "zhegefangchengzenmejie")
        self.assertEqual((flags & 1, page), (1, 1))
        self.assertEqual(len(set(first) & set(second)), 0)
        self.assertEqual(len(first) + len(second), 13)

        # A page beyond the end is the last page.
        _found, flags, page, _image = self.ask(9, "zhegefangchengzenmejie")
        self.assertEqual((flags, page), (1, 1))

    def test_every_frame_fits_the_page_buffers(self):
        found, _flags, _page, _image = self.ask(0, "zhe")
        for text, consumed in found:
            self.assertLess(len(text.encode("utf-8")), 48)
            self.assertTrue(0 < consumed < 256)
        self.assertLess(len(self.sent[0][3]), 4096)

    def test_no_match_and_dictionary_still_loading(self):
        self.host.ime = FakeIME([])
        found, flags, _page, image = self.ask(0, "xq")
        self.assertEqual((found, flags), ([], 0))
        self.assertLessEqual(image[3], 40)
        self.host.ime = None
        found, flags, _page, _image = self.ask(0, "ni")
        self.assertEqual((found, flags), ([], 0))
        self.host.on_ime(6, b"")  # nothing to answer, nothing raised
        self.host.on_ime_pick(b"ni\t\xe4\xbd\xa0\t")

    def test_picks_are_learned_and_pieces_become_a_phrase(self):
        engine = self.host.ime
        self.host.on_ime_pick("jie\t解\tfangcheng".encode("utf-8"))
        self.host.on_ime_pick("fangcheng\t方程\t".encode("utf-8"))
        self.assertEqual(engine.learned,
                         [("jie", "解"), ("fangcheng", "方程"), ("jiefangcheng", "解方程")])
        # Letters typed after a partial pick still continue the phrase ...
        engine.learned.clear()
        self.host.on_ime_pick("wo\t我\txiang".encode("utf-8"))
        self.host.on_ime_pick("xiangzhidao\t想知道\t".encode("utf-8"))
        self.assertEqual(engine.learned[-1], ("woxiangzhidao", "我想知道"))
        # ... but a composition that was erased and started again does not.
        engine.learned.clear()
        self.host.on_ime_pick("wo\t我\txiang".encode("utf-8"))
        self.host.on_ime_pick("ni\t你\t".encode("utf-8"))
        self.assertEqual(engine.learned, [("wo", "我"), ("ni", "你")])
        # A whole-input pick is learned once.
        engine.learned.clear()
        self.host.on_ime_pick("nihao\t你好\t".encode("utf-8"))
        self.assertEqual(engine.learned, [("nihao", "你好")])
        for damaged in (b"", b"ni", b"\t\t", b"\xff\xfe\t\xff"):
            self.host.on_ime_pick(damaged)


class FakeWebSession:
    def __init__(self, log):
        self.log = log

    def describe(self, name, arguments):
        return f"Searching: {arguments}"

    def call(self, name, arguments):
        self.log.append((name, arguments))
        return "1. Example\n   https://example.com/\n   a result"


class FakeWeb:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.questions: list[str] = []

    def session(self, user_text=""):
        self.questions.append(user_text)
        return FakeWebSession(self.calls)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[tuple[int, int, int, bytes]] = []
        self.home = tempfile.TemporaryDirectory()
        self.host = PageHost(lambda *frame: self.sent.append(frame), EchoBackend(),
                             home=Path(self.home.name))
        self.host.web = FakeWeb()
        self.host.on_hello(7, b"v=2;n=0;sid=0;mv=0")
        self.sent.clear()

    def tearDown(self):
        self.home.cleanup()

    def kinds(self) -> list[int]:
        return [imagecodec.decode_block(frame[3])[0] for frame in self.sent if frame[0] == OP_BLOCK]

    def test_the_model_is_given_the_tools_and_progress_is_shown(self):
        self.assertTrue(self.host.web_enabled)
        self.host.on_request(9, b"#think:off \x1fsearch ti nspire")
        self.assertEqual(self.host.web.questions, ["search ti nspire"])
        self.assertEqual(self.host.web.calls, [("web_search", '{"query": "ti nspire"}')])
        # the question, a note about the search, the answer
        self.assertEqual(self.kinds(), [imagecodec.KIND_USER, imagecodec.KIND_INFO,
                                        imagecodec.KIND_ASSISTANT])
        stored = self.host.store.active().messages
        self.assertIn("example.com", stored[-1]["content"])
        system = self.host._context(self.host.store.active().id, None, "x")[0]["content"]
        self.assertIn("web_search", system)

    def test_the_menu_switches_it_off_and_the_choice_is_kept(self):
        version = self.host.menu_version
        self.host.on_action(b"web.toggle")
        self.assertFalse(self.host.web_enabled)
        self.assertNotEqual(self.host.menu_version, version)   # the page fetches the menu again
        self.assertIn(OP_SCREEN, [frame[0] for frame in self.sent])
        self.sent.clear()
        self.host.on_request(10, b"#think:off \x1fsearch ti nspire")
        self.assertEqual(self.host.web.calls, [])
        self.assertEqual(self.kinds(), [imagecodec.KIND_USER, imagecodec.KIND_ASSISTANT])
        system = self.host._context(self.host.store.active().id, None, "x")[0]["content"]
        self.assertNotIn("web_search", system)

        again = PageHost(lambda *frame: None, EchoBackend(), home=Path(self.home.name))
        again.web = FakeWeb()
        self.assertFalse(again.web_enabled)
        again.on_action(b"web.toggle")
        self.assertTrue(again.web_enabled)

    def test_without_tools_the_menu_has_no_switch(self):
        self.host.web = None
        self.assertFalse(self.host.web_enabled)
        self.host.on_action(b"web.toggle")   # an old menu still on the page: ignored
        self.assertFalse(self.host.web_enabled)
        self.sent.clear()
        self.host.on_request(11, b"#think:off \x1fsearch ti nspire")
        self.assertEqual(self.kinds(), [imagecodec.KIND_USER, imagecodec.KIND_ASSISTANT])

    def test_notes_are_limited(self):
        class Chatty:
            supports_tools = True

            def complete(inner, messages, effort=None, tools=None, progress=None):
                for number in range(30):
                    progress(f"Searching: {number}")
                return "done"

        self.host.backend = Chatty()
        self.host.on_request(12, b"#think:off \x1fquestion")
        self.assertEqual(self.kinds().count(imagecodec.KIND_INFO), 8)


if __name__ == "__main__":
    unittest.main()
