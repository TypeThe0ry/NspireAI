from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.bridge import Bridge, EchoBackend
from bridge.transport import DirectoryTransport


class BridgeEchoTests(unittest.TestCase):
    def test_echo_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            transport.write("request.tns", "Hello\n第二行".encode("utf-8"))
            transport.write("request.id.tns", b"42")
            self.assertEqual(bridge.step(), "42")
            self.assertEqual(transport.read("response.id.tns"), b"42")
            self.assertEqual(transport.read("response.tns"), "Mac received: Hello\n第二行".encode("utf-8"))
            self.assertIsNone(bridge.step())

    def test_long_text_and_repeated_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            prompt = "x" * 10000
            transport.write("request.tns", prompt.encode())
            transport.write("request.id.tns", b"1")
            self.assertEqual(bridge.step(), "1")
            transport.write("request.tns", b"new")
            transport.write("request.id.tns", b"2")
            self.assertEqual(bridge.step(), "2")
            self.assertEqual(transport.read("response.tns"), b"Mac received: new")

    def test_oversized_response_is_rejected_before_calculator_limit(self):
        class HugeBackend:
            def reset(self):
                pass

            def answer(self, prompt):
                return "x" * (256 * 1024 + 1)

        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, HugeBackend())
            transport.write("request.tns", b"short")
            transport.write("request.id.tns", b"1-1")
            self.assertEqual(bridge.step(), "1-1")
            self.assertLess(len(transport.read("response.tns")), 256 * 1024)

    def test_incomplete_request_does_not_publish_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            transport.write("request.id.tns", b"7")
            self.assertIsNone(bridge.step())
            self.assertIsNone(transport.read("response.id.tns"))

    def test_conversation_namespace_resets_backend(self):
        class RecordingBackend(EchoBackend):
            def __init__(self):
                self.reset_count = 0

            def reset(self):
                self.reset_count += 1

        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            backend = RecordingBackend()
            bridge = Bridge(transport, backend)
            for request_id in (b"1-1", b"1-2", b"2-1"):
                transport.write("request.tns", b"hello")
                transport.write("request.id.tns", request_id)
                self.assertIsNotNone(bridge.step())
            self.assertEqual(backend.reset_count, 2)

    def test_failed_ready_marker_upload_does_not_repeat_backend(self):
        class FlakyTransport(DirectoryTransport):
            def __init__(self, root):
                super().__init__(root)
                self.fail_ready_once = True

            def write(self, name, data):
                if name == "response.id.tns" and self.fail_ready_once:
                    self.fail_ready_once = False
                    raise OSError("simulated USB disconnect")
                return super().write(name, data)

        class CountingBackend(EchoBackend):
            def __init__(self):
                self.calls = 0

            def answer(self, prompt):
                self.calls += 1
                return super().answer(prompt)

        with tempfile.TemporaryDirectory() as tmp:
            transport = FlakyTransport(Path(tmp))
            backend = CountingBackend()
            bridge = Bridge(transport, backend)
            transport.write("request.tns", b"once")
            transport.write("request.id.tns", b"1-1")
            with self.assertRaises(OSError):
                bridge.step()
            self.assertEqual(backend.calls, 1)
            self.assertEqual(bridge.step(), "1-1")
            self.assertEqual(backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
