from __future__ import annotations

import unittest

from bridge.navnet_bridge import (
    NavNetBridge,
    OP_CANCEL,
    OP_NEW,
    OP_PING,
    OP_PONG,
    OP_REQUEST,
    OP_RESPONSE,
)
from bridge.protocol import decode
from bridge.protocol import FragmentReassembler


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
    def test_ping_gets_pong_with_same_ids(self):
        bridge, _, sent = make_bridge()
        bridge.handle(OP_PING, 7, 3, b"PING")
        self.assertEqual(decode(sent[0]), (OP_PONG, 7, 3, b"PONG"))

    def test_request_is_answered_and_deduplicated(self):
        bridge, backend, sent = make_bridge()
        bridge.handle(OP_REQUEST, 11, 2, "你好".encode())
        bridge.handle(OP_REQUEST, 11, 2, "你好".encode())
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
        self.assertEqual(backend.reset_count, 1)
        self.assertEqual(bridge.conversation_id, 9)

    def test_large_response_is_fragmented(self):
        bridge, backend, sent = make_bridge()
        backend.answer = lambda prompt: "x" * 5000
        bridge.handle(OP_REQUEST, 12, 4, b"long")
        self.assertGreater(len(sent), 1)
        reassembler = FragmentReassembler()
        complete = None
        for frame in sent:
            opcode, request_id, conversation_id, payload = decode(frame)
            self.assertEqual((opcode, request_id, conversation_id), (8, 12, 4))
            complete = reassembler.add(conversation_id, request_id, payload)
        self.assertEqual(complete, (OP_RESPONSE, b"x" * 5000))


if __name__ == "__main__":
    unittest.main()
