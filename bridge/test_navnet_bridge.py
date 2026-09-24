from __future__ import annotations

import unittest
import threading
from unittest.mock import patch

from bridge.navnet_bridge import (
    NavNetBridge,
    OP_CANCEL,
    OP_NEW,
    OP_PING,
    OP_PONG,
    OP_REQUEST,
    OP_RESPONSE,
    OP_ERROR,
    OP_FRAGMENT,
    MAX_RESPONSE_BYTES,
)
from bridge.protocol import decode
from bridge.protocol import FragmentReassembler, fragment


class RecordingBackend:
    def __init__(self):
        self.prompts: list[str] = []
        self.reset_count = 0

    def answer(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"AI: {prompt}"

    def reset(self) -> None:
        self.reset_count += 1


def make_bridge() -> tuple[NavNetBridge, RecordingBackend, list[bytes]]:
    bridge = NavNetBridge.__new__(NavNetBridge)
    backend = RecordingBackend()
    sent: list[bytes] = []
    bridge.backend = backend
    bridge.processed = set()
    bridge.canceled = set()
    bridge.conversation_id = None
    bridge.send = sent.append  # type: ignore[method-assign]
    return bridge, backend, sent


class NavNetBridgeTests(unittest.TestCase):
    def test_backend_startup_failure_does_not_open_usb_helper(self):
        with patch("bridge.navnet_bridge.make_backend", side_effect=RuntimeError("missing API key")):
            with patch("bridge.navnet_bridge.subprocess.Popen") as popen:
                with self.assertRaisesRegex(RuntimeError, "missing API key"):
                    NavNetBridge("helper", "openai", "model", None, 1.0)
                popen.assert_not_called()

    def test_session_ping_fragmented_request_response_new_and_cancel(self):
        """Exercise the ordered host-side session without a USB device.

        This is deliberately a wire-level test: the request is delivered as
        NavNet fragments, while the response is collected from the bridge's
        outgoing frames and reassembled exactly as the calculator does.
        """
        bridge, backend, sent = make_bridge()
        bridge.fragments = FragmentReassembler()

        bridge.handle(OP_PING, 1, 7, b"PING")
        self.assertEqual(decode(sent.pop(0)), (OP_PONG, 1, 7, b"PONG"))

        prompt = ("fragmented-" + "问" * 180).encode("utf-8")
        frames = list(fragment(OP_REQUEST, 2, 7, prompt))
        request_reassembly = FragmentReassembler()
        for frame in frames:
            opcode, request_id, conversation_id, payload = decode(frame)
            self.assertEqual(opcode, 8)
            complete = request_reassembly.add(conversation_id, request_id, payload)
            # The bridge receives the same fragments above; this local
            # reassembly only verifies the generated request sequence.
            bridge.handle_frame(frame)
            if complete is not None:
                self.assertEqual(complete, (OP_REQUEST, prompt))

        bridge.executor.shutdown(wait=True)
        out = [decode(frame) for frame in sent]
        response = FragmentReassembler()
        complete_response = None
        for opcode, request_id, conversation_id, payload in out:
            self.assertEqual((request_id, conversation_id), (2, 7))
            if opcode == OP_FRAGMENT:
                complete_response = response.add(conversation_id, request_id, payload)
            else:
                complete_response = (opcode, payload)
        self.assertEqual(complete_response, (OP_RESPONSE, b"AI: " + prompt))
        self.assertEqual(backend.prompts, [prompt.decode("utf-8")])

        # A new conversation invalidates the old namespace; a canceled request
        # must not reach the backend or produce a late response.
        sent.clear()
        bridge.executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=1)
        bridge.handle(OP_NEW, 0, 8, b"")
        bridge.handle(OP_CANCEL, 3, 8, b"")
        bridge.handle(OP_REQUEST, 3, 8, b"canceled")
        bridge.executor.shutdown(wait=True)
        self.assertEqual(bridge.conversation_id, 8)
        self.assertEqual(backend.prompts, [prompt.decode("utf-8")])
        self.assertEqual(sent, [])

    def test_response_byte_limit(self):
        for answer, expected in [
            ("x" * MAX_RESPONSE_BYTES, OP_RESPONSE),
            ("x" * (MAX_RESPONSE_BYTES + 1), OP_ERROR),
            ("\u4f60" * 6000, OP_ERROR),
            ("x" * 70000, OP_ERROR),
        ]:
            with self.subTest(size=len(answer.encode())):
                bridge, backend, sent = make_bridge()
                backend.answer = lambda prompt: answer
                bridge.handle(OP_REQUEST, 1, 1, b"test")
                bridge.executor.shutdown(wait=True)
                reassembler = FragmentReassembler()
                result = None
                for frame in sent:
                    opcode, rid, cid, payload = decode(frame)
                    self.assertEqual((rid, cid), (1, 1))
                    result = reassembler.add(cid, rid, payload) if opcode == 8 else (opcode, payload)
                self.assertIsNotNone(result)
                self.assertEqual(result[0], expected)
                self.assertLessEqual(len(result[1]), MAX_RESPONSE_BYTES)
                if expected == OP_RESPONSE:
                    self.assertEqual(result[1], answer.encode())

    def test_ping_gets_pong_with_same_ids(self):
        bridge, _, sent = make_bridge()
        bridge.handle(OP_PING, 7, 3, b"PING")
        self.assertEqual(decode(sent[0]), (OP_PONG, 7, 3, b"PONG"))

    def test_request_is_answered_and_deduplicated(self):
        bridge, backend, sent = make_bridge()
        bridge.handle(OP_REQUEST, 11, 2, "你好".encode())
        bridge.handle(OP_REQUEST, 11, 2, "你好".encode())
        bridge.executor.shutdown(wait=True)
        self.assertEqual(backend.prompts, ["你好"])
        self.assertEqual(decode(sent[0]), (OP_RESPONSE, 11, 2, "AI: 你好".encode()))

    def test_cancel_prevents_late_request(self):
        bridge, backend, sent = make_bridge()
        bridge.handle(OP_CANCEL, 4, 1, b"")
        bridge.handle(OP_REQUEST, 4, 1, b"ignored")
        self.assertEqual(backend.prompts, [])
        self.assertEqual(sent, [])

    def test_new_conversation_resets_backend(self):
        bridge, backend, _ = make_bridge()
        bridge.handle(OP_NEW, 0, 9, b"")
        bridge.executor.shutdown(wait=True)
        self.assertEqual(backend.reset_count, 1)
        self.assertEqual(bridge.conversation_id, 9)

    def test_large_response_is_fragmented(self):
        bridge, backend, sent = make_bridge()
        backend.answer = lambda prompt: "x" * 5000
        bridge.handle(OP_REQUEST, 12, 4, b"long")
        bridge.executor.shutdown(wait=True)
        self.assertGreater(len(sent), 1)
        reassembler = FragmentReassembler()
        complete = None
        for frame in sent:
            opcode, request_id, conversation_id, payload = decode(frame)
            self.assertEqual((opcode, request_id, conversation_id), (8, 12, 4))
            complete = reassembler.add(conversation_id, request_id, payload)
        self.assertEqual(complete, (OP_RESPONSE, b"x" * 5000))

    def test_new_chat_during_slow_answer_does_not_block_ping(self):
        bridge, backend, sent = make_bridge()
        started, release, handled = threading.Event(), threading.Event(), threading.Event()

        def answer(prompt):
            if prompt == "old":
                started.set()
                release.wait(3)
            return "AI: " + prompt

        backend.answer = answer
        bridge.handle(OP_REQUEST, 1, 1, b"old")
        self.assertTrue(started.wait(1))

        def receive():
            bridge.handle(OP_NEW, 0, 2, b"")
            bridge.handle(OP_PING, 2, 2, b"PING")
            bridge.handle(OP_REQUEST, 3, 2, b"new")
            handled.set()

        receiver = threading.Thread(target=receive)
        receiver.start()
        try:
            self.assertTrue(handled.wait(1), "USB receiver blocked on model answer")
            self.assertEqual(decode(sent[0]), (OP_PONG, 2, 2, b"PONG"))
        finally:
            release.set()
            receiver.join(4)
            bridge.executor.shutdown(wait=True)
        self.assertEqual([decode(f) for f in sent], [
            (OP_PONG, 2, 2, b"PONG"),
            (OP_RESPONSE, 3, 2, b"AI: new"),
        ])
        self.assertEqual(backend.reset_count, 2)


if __name__ == "__main__":
    unittest.main()
