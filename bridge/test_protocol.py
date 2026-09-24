import unittest
try:
    from .protocol import MAX_PAYLOAD, FragmentReassembler, decode, encode, fragment
except ImportError:  # direct `python bridge/test_protocol.py`
    from protocol import MAX_PAYLOAD, FragmentReassembler, decode, encode, fragment

class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        frame = encode(1, 42, 7, "你好".encode())
        self.assertEqual(decode(frame), (1, 42, 7, "你好".encode()))

    def test_rejects_corruption(self):
        with self.assertRaises(ValueError): decode(b"bad")
        with self.assertRaises(ValueError): decode(encode(1, 1, 1, b"x")[:-1])

    def test_payload_budget_fits_documented_navnet_service_limit(self):
        # NavNet service data is limited to 254 bytes; the NSAI header is 16.
        frame = encode(1, 1, 1, b"x" * MAX_PAYLOAD)
        self.assertLessEqual(len(frame), 254)
        with self.assertRaises(ValueError):
            encode(1, 1, 1, b"x" * (MAX_PAYLOAD + 1))

    def test_long_message_fragments_and_reassembles_in_order(self):
        payload = ("长文本" * 800).encode("utf-8")
        frames = list(fragment(3, 42, 7, payload))
        self.assertGreater(len(frames), 1)
        reassembler = FragmentReassembler()
        complete = None
        for frame in frames:
            opcode, request_id, conversation_id, fragment_payload = decode(frame)
            self.assertEqual((request_id, conversation_id), (42, 7))
            self.assertEqual(opcode, 8)
            complete = reassembler.add(conversation_id, request_id, fragment_payload)
        self.assertEqual(complete, (3, payload))

if __name__ == "__main__": unittest.main()
