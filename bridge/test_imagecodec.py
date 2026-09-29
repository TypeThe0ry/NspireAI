import random
import unittest

from PIL import Image

try:
    from . import imagecodec
    from .imagecodec import (
        ACTION_CLOSE, ACTION_COMMAND, ACTION_GOTO, ACTION_INSERT, ACTION_SEND, ACTION_SEND_KEEP,
        ENCODING_PACKBITS, ENCODING_RAW, ScreenKey, decode_block, decode_screen, encode_block,
        encode_blocks, encode_screen, packbits_decode, packbits_encode, quantize, row_bytes,
        split_image, unpack,
    )
except ImportError:  # direct `python bridge/test_imagecodec.py`
    import imagecodec
    from imagecodec import (
        ACTION_CLOSE, ACTION_COMMAND, ACTION_GOTO, ACTION_INSERT, ACTION_SEND, ACTION_SEND_KEEP,
        ENCODING_PACKBITS, ENCODING_RAW, ScreenKey, decode_block, decode_screen, encode_block,
        encode_blocks, encode_screen, packbits_decode, packbits_encode, quantize, row_bytes,
        split_image, unpack,
    )


def reference_decode(data: bytes) -> bytes:
    """Independent PackBits decoder written straight from the specification."""
    out = bytearray()
    i = 0
    while i < len(data):
        control = data[i]
        i += 1
        if control <= 127:
            out += data[i:i + control + 1]
            i += control + 1
        elif control >= 129:
            out += bytes([data[i]]) * (257 - control)
            i += 1
    return bytes(out)


def noise_image(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    return Image.frombytes("L", (width, height), bytes(rng.randrange(256) for _ in range(width * height)))


def level_image(width: int, height: int, bpp: int, seed: int) -> Image.Image:
    """An image that only uses gray values a `bpp` image can represent exactly."""
    rng = random.Random(seed)
    top = (1 << bpp) - 1
    values = bytes(rng.randrange(top + 1) * 255 // top for _ in range(width * height))
    return Image.frombytes("L", (width, height), values)


class PackBitsTests(unittest.TestCase):
    def assert_round_trip(self, data: bytes) -> bytes:
        encoded = packbits_encode(data)
        self.assertNotIn(128, self.control_bytes(encoded))
        self.assertEqual(packbits_decode(encoded, len(data)), data)
        self.assertEqual(reference_decode(encoded), data)
        return encoded

    @staticmethod
    def control_bytes(encoded: bytes) -> list[int]:
        controls = []
        i = 0
        while i < len(encoded):
            control = encoded[i]
            controls.append(control)
            i += 1 + (control + 1 if control < 128 else 1)
        return controls

    def test_empty(self):
        self.assertEqual(packbits_encode(b""), b"")
        self.assertEqual(packbits_decode(b"", 0), b"")

    def test_known_vectors(self):
        self.assertEqual(packbits_encode(b"\xAA"), b"\x00\xAA")
        self.assertEqual(packbits_encode(b"\xAA\xAA"), b"\xFF\xAA")
        self.assertEqual(packbits_encode(b"\xAA" * 128), b"\x81\xAA")
        self.assertEqual(packbits_encode(b"\x01\x02\x03"), b"\x02\x01\x02\x03")
        # The classic Apple TN1023 example.
        data = bytes.fromhex("AAAAAA80002AAAAAAAAA80002A22AAAAAAAAAAAAAAAAAAAA")
        encoded = bytes.fromhex("FEAA0280002AFDAA0380002A22F7AA")
        self.assertEqual(packbits_decode(encoded, len(data)), data)
        self.assertEqual(packbits_encode(data), encoded)

    def test_all_same(self):
        for length in (1, 2, 3, 127, 128):
            encoded = self.assert_round_trip(b"\x5A" * length)
            self.assertLessEqual(len(encoded), 2)

    def test_long_runs(self):
        for length in (129, 130, 255, 256, 257, 1000, 128 * 7 + 1):
            encoded = self.assert_round_trip(b"\xFF" * length)
            self.assertLessEqual(len(encoded), 2 * (length // 128 + 1))

    def test_long_literal_runs(self):
        for length in (127, 128, 129, 130, 256, 257, 1000):
            data = bytes((i * 7 + i // 256) % 251 for i in range(length))
            encoded = self.assert_round_trip(data)
            self.assertEqual(len(encoded), length + (length + 127) // 128)

    def test_random(self):
        rng = random.Random(1234)
        for _ in range(200):
            length = rng.randrange(0, 600)
            alphabet = rng.choice((2, 3, 16, 256))
            data = bytearray()
            while len(data) < length:
                data += bytes([rng.randrange(alphabet)]) * rng.choice((1, 1, 1, 2, 3, 5, 40, 200))
            self.assert_round_trip(bytes(data[:length]))

    def test_run_of_two_does_not_split_literals(self):
        data = b"\x01\x02\x02\x03"
        self.assertEqual(packbits_encode(data), b"\x03" + data)

    def test_decode_skips_noop(self):
        self.assertEqual(packbits_decode(b"\x80\x00\x41\x80", 1), b"A")

    def test_decode_overrun_and_underrun(self):
        encoded = packbits_encode(b"abc" + b"\x00" * 10)
        with self.assertRaises(ValueError):
            packbits_decode(encoded, 12)
        with self.assertRaises(ValueError):
            packbits_decode(encoded, 14)
        with self.assertRaises(ValueError):
            packbits_decode(encoded[:-1], 13)      # repeat run without its byte
        with self.assertRaises(ValueError):
            packbits_decode(b"\x05ab", 6)          # literal run cut short
        with self.assertRaises(ValueError):
            packbits_decode(b"", 1)
        with self.assertRaises(ValueError):
            packbits_decode(b"", -1)


class PixelTests(unittest.TestCase):
    def test_row_bytes(self):
        self.assertEqual(row_bytes(320, 4), 160)
        self.assertEqual(row_bytes(319, 4), 160)
        self.assertEqual(row_bytes(1, 4), 1)
        self.assertEqual(row_bytes(5, 2), 2)
        self.assertEqual(row_bytes(9, 1), 2)
        with self.assertRaises(ValueError):
            row_bytes(8, 3)

    def test_packing_is_msb_first(self):
        image = Image.new("L", (3, 2), 255)
        image.putpixel((0, 0), 0)
        image.putpixel((1, 0), 17 * 5)
        image.putpixel((2, 1), 17 * 9)
        # 4 bpp: rows of two bytes, the unused low nibble of each row is zero.
        self.assertEqual(quantize(image, 4), bytes([0x05, 0xF0, 0xFF, 0x90]))
        # 2 bpp: 85 -> 1, 153 -> round(1.8) = 2.
        self.assertEqual(quantize(image, 2), bytes([0b00011100, 0b11111000]))
        # 1 bpp: 85 -> 0, 153 -> 1.
        self.assertEqual(quantize(image, 1), bytes([0b00100000, 0b11100000]))

    def test_quantize_rounds_to_nearest_level(self):
        for bpp in (1, 2, 4):
            top = (1 << bpp) - 1
            ramp = Image.frombytes("L", (256, 1), bytes(range(256)))
            levels = unpack(quantize(ramp, bpp), 256, 1, bpp).tobytes()
            for value in range(256):
                self.assertEqual(levels[value], round(value * top / 255) * 255 // top, (bpp, value))

    def test_round_trip_for_odd_widths(self):
        for bpp in (1, 2, 4):
            for width in (1, 2, 3, 5, 7, 9, 13, 31, 33, 319, 320):
                height = 3
                image = level_image(width, height, bpp, seed=width * 8 + bpp)
                raw = quantize(image, bpp)
                self.assertEqual(len(raw), row_bytes(width, bpp) * height)
                restored = unpack(raw, width, height, bpp)
                self.assertEqual(restored.mode, "L")
                self.assertEqual(restored.size, (width, height))
                self.assertEqual(restored.tobytes(), image.tobytes(), (bpp, width))

    def test_quantize_converts_other_modes(self):
        image = Image.new("RGB", (4, 1), (255, 255, 255))
        image.putpixel((1, 0), (0, 0, 0))
        self.assertEqual(quantize(image, 4), bytes([0xF0, 0xFF]))

    def test_padding_bits_are_zero(self):
        self.assertEqual(quantize(Image.new("L", (3, 1), 255), 1), bytes([0b11100000]))
        self.assertEqual(quantize(Image.new("L", (3, 1), 255), 2), bytes([0b11111100]))
        self.assertEqual(quantize(Image.new("L", (3, 1), 255), 4), bytes([0xFF, 0xF0]))

    def test_unpack_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            unpack(b"\x00" * 3, 3, 1, 4)
        with self.assertRaises(ValueError):
            unpack(b"", 0, 1, 4)
        with self.assertRaises(ValueError):
            unpack(b"\x00", 1, 1, 8)


class BlockTests(unittest.TestCase):
    def test_header_layout(self):
        image = Image.new("L", (300, 2), 255)
        payload = encode_block(1, image, 0x01020304, bpp=4)
        self.assertEqual(
            payload[:12],
            bytes([0x01, 0x04, 0x01, 0x2C, 0x00, 0x02, 0x01, 0x00, 0x01, 0x02, 0x03, 0x04]),
        )
        # 300 bytes of 0xFF: runs of 128, 128 and 44.
        self.assertEqual(payload[12:], bytes([0x81, 0xFF, 0x81, 0xFF, 0xD5, 0xFF]))

    def test_raw_is_kept_when_packbits_does_not_help(self):
        image = Image.frombytes("L", (4, 1), bytes([0, 17, 34, 51]))
        payload = encode_block(0, image, 7, bpp=4)
        self.assertEqual(payload, bytes([0, 4, 0, 4, 0, 1, ENCODING_RAW, 0, 0, 0, 0, 7, 0x01, 0x23]))

    def test_round_trip(self):
        for bpp in (1, 2, 4):
            for width, height in ((1, 1), (7, 5), (319, 9), (320, 40)):
                image = level_image(width, height, bpp, seed=width + height + bpp)
                payload = encode_block(2, image, 99, bpp=bpp)
                kind, got_bpp, got_width, got_height, block_id, restored = decode_block(payload)
                self.assertEqual((kind, got_bpp, got_width, got_height, block_id), (2, bpp, width, height, 99))
                self.assertEqual(restored.tobytes(), image.tobytes())

    def test_round_trip_of_compressible_image(self):
        image = Image.new("L", (320, 64), 255)
        for x in range(40, 200):
            image.putpixel((x, 30), 0)
        payload = encode_block(0, image, 1)
        self.assertEqual(payload[6], ENCODING_PACKBITS)
        self.assertLess(len(payload), 200)
        self.assertEqual(decode_block(payload)[5].tobytes(), image.tobytes())

    def test_noise_round_trips_through_quantization(self):
        image = noise_image(37, 11, seed=5)
        restored = decode_block(encode_block(0, image, 0, bpp=4))[5]
        self.assertEqual(restored.tobytes(), unpack(quantize(image, 4), 37, 11, 4).tobytes())

    def test_limits(self):
        good = Image.new("L", (8, 8), 255)
        with self.assertRaises(ValueError):
            encode_block(0, Image.new("L", (321, 1), 255), 0)
        with self.assertRaises(ValueError):
            encode_block(0, Image.new("L", (1, 65536), 255), 0)
        with self.assertRaises(ValueError):
            encode_block(0, good, 0, bpp=3)
        with self.assertRaises(ValueError):
            encode_block(0, good, 0, bpp=8)
        with self.assertRaises(ValueError):
            encode_block(256, good, 0)
        with self.assertRaises(ValueError):
            encode_block(-1, good, 0)
        with self.assertRaises(ValueError):
            encode_block(0, good, 1 << 32)
        with self.assertRaises(ValueError):
            encode_block(0, good, -1)
        self.assertEqual(decode_block(encode_block(0, Image.new("L", (320, 1), 0), 0xFFFFFFFF))[4], 0xFFFFFFFF)

    def test_flags_byte(self):
        image = level_image(16, 4, 4, seed=2)
        plain = encode_block(0, image, 5)
        self.assertEqual(plain[7], 0)
        self.assertEqual(imagecodec.block_flags(plain), 0)
        flagged = encode_block(0, image, 5, flags=imagecodec.BLOCK_FLAG_CONTINUATION)
        self.assertEqual(flagged[7], 1)
        self.assertEqual(flagged[:7] + flagged[8:], plain[:7] + plain[8:])
        # The page host sets the bit on an encoded payload; decoding ignores it.
        patched = bytearray(plain)
        patched[7] |= 1
        self.assertEqual(bytes(patched), flagged)
        self.assertEqual(decode_block(bytes(patched))[:5], (0, 4, 16, 4, 5))
        self.assertEqual(decode_block(bytes(patched))[5].tobytes(), image.tobytes())
        with self.assertRaises(ValueError):
            encode_block(0, image, 5, flags=256)
        with self.assertRaises(ValueError):
            imagecodec.block_flags(plain[:7])

    def test_tallest_block(self):
        payload = encode_block(0, Image.new("L", (1, 65535), 255), 3, bpp=1)
        self.assertEqual(payload[4:6], b"\xFF\xFF")
        self.assertEqual(decode_block(payload)[3], 65535)

    def test_decode_rejects_corruption(self):
        payload = bytearray(encode_block(0, level_image(16, 4, 4, seed=1), 1))
        with self.assertRaises(ValueError):
            decode_block(bytes(payload[:11]))
        with self.assertRaises(ValueError):
            decode_block(bytes(payload[:-1]))
        with self.assertRaises(ValueError):
            decode_block(bytes(payload) + b"\x00\x00")
        for offset, value in ((1, 3), (6, 2)):
            broken = bytearray(payload)
            broken[offset] = value
            with self.assertRaises(ValueError):
                decode_block(bytes(broken))
        broken = bytearray(payload)
        broken[2:4] = (321).to_bytes(2, "big")
        with self.assertRaises(ValueError):
            decode_block(bytes(broken))

    def test_split_image_prefers_blank_rows(self):
        image = Image.new("L", (16, 100), 255)
        for y in list(range(0, 38)) + list(range(42, 100)):
            image.putpixel((3, y), 0)
        strips = list(split_image(image, 50))
        self.assertEqual([strip.height for strip in strips][0], 42)
        self.assertEqual(sum(strip.height for strip in strips), 100)
        self.assertTrue(all(strip.height <= 50 for strip in strips))
        joined = b"".join(strip.tobytes() for strip in strips)
        self.assertEqual(joined, image.tobytes())

    def test_encode_blocks_respects_payload_limit(self):
        image = noise_image(320, 300, seed=9)
        payloads = encode_blocks(1, image, 10, bpp=4, max_payload=8000, max_rows=120)
        self.assertGreater(len(payloads), 1)
        rows = []
        for index, payload in enumerate(payloads):
            self.assertLessEqual(len(payload), 8000)
            kind, _bpp, width, height, block_id, strip = decode_block(payload)
            self.assertEqual((kind, width, block_id), (1, 320, 10 + index))
            self.assertEqual(imagecodec.block_flags(payload), 1 if index else 0)
            rows.append(strip.tobytes())
        self.assertEqual(b"".join(rows), unpack(quantize(image, 4), 320, 300, 4).tobytes())
        shared = encode_blocks(1, image, 10, max_payload=8000, max_rows=120, same_id=True)
        self.assertEqual([decode_block(payload)[4] for payload in shared], [10] * len(payloads))


class ScreenTests(unittest.TestCase):
    def keys(self):
        return [
            ScreenKey("1", ACTION_COMMAND, b"factorize|factorize"),
            ScreenKey("2", ACTION_INSERT, b"\\frac{"),
            ScreenKey("0", ACTION_GOTO, bytes([3])),
            ScreenKey("a", ACTION_SEND, b"session:select:12"),
            ScreenKey("d", ACTION_SEND_KEEP, b"session:delete:12"),
            ScreenKey("q", ACTION_CLOSE),
        ]

    def test_header_layout(self):
        image = Image.new("L", (320, 222), 255)
        keys = [ScreenKey("1", ACTION_GOTO, bytes([2])), ScreenKey("a", ACTION_INSERT, b"\\pi")]
        payload = encode_screen(7, 1, keys, image, bpp=2)
        self.assertEqual(payload[:10], bytes([0x07, 0x01, 0x02, 0x02, 0x01, 0x40, 0x00, 0xDE, 0x01, 0x00]))
        self.assertEqual(payload[10:14], bytes([0x31, 0x01, 0x01, 0x02]))
        self.assertEqual(payload[14:20], bytes([0x61, 0x02, 0x03, 0x5C, 0x70, 0x69]))
        # 80 bytes per row * 222 rows = 17760 = 138 * 128 + 96 bytes of 0xFF.
        self.assertEqual(payload[20:], bytes([0x81, 0xFF]) * 138 + bytes([0xA1, 0xFF]))

    def test_round_trip(self):
        for bpp in (1, 2, 4):
            image = level_image(317, 50, bpp, seed=bpp)
            payload = encode_screen(200, 0, self.keys(), image, bpp=bpp)
            screen_id, flags, keys, got_bpp, width, height, restored = decode_screen(payload)
            self.assertEqual((screen_id, flags, got_bpp, width, height), (200, 0, bpp, 317, 50))
            self.assertEqual(keys, self.keys())
            self.assertEqual(restored.tobytes(), image.tobytes())

    def test_no_keys(self):
        image = Image.new("L", (10, 10), 0)
        screen_id, flags, keys, *_rest, restored = decode_screen(encode_screen(1, 1, [], image))
        self.assertEqual((screen_id, flags, keys), (1, 1, []))
        self.assertEqual(restored.tobytes(), image.tobytes())

    def test_string_arguments_are_encoded_as_ascii(self):
        self.assertEqual(ScreenKey("1", ACTION_INSERT, "\\sqrt{").arg, b"\\sqrt{")
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_INSERT, "中")

    def test_key_validation(self):
        with self.assertRaises(ValueError):
            ScreenKey("", ACTION_CLOSE)
        with self.assertRaises(ValueError):
            ScreenKey("ab", ACTION_CLOSE)
        with self.assertRaises(ValueError):
            ScreenKey("中", ACTION_CLOSE)
        with self.assertRaises(ValueError):
            ScreenKey("1", 6)
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_GOTO, b"")
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_GOTO, bytes([0]))
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_GOTO, bytes([1, 2]))
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_INSERT, b"x" * 256)
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_INSERT, "é".encode())
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_COMMAND, b"no-separator")
        with self.assertRaises(ValueError):
            ScreenKey("1", ACTION_COMMAND, b"id|label-is-too-long")
        ScreenKey("1", ACTION_COMMAND, b"id|twelve_chars")
        ScreenKey("1", ACTION_INSERT, b"x" * 255)

    def test_limits(self):
        image = Image.new("L", (320, 222), 255)
        with self.assertRaises(ValueError):
            encode_screen(0, 0, [], image)
        with self.assertRaises(ValueError):
            encode_screen(256, 0, [], image)
        with self.assertRaises(ValueError):
            encode_screen(1, 256, [], image)
        with self.assertRaises(ValueError):
            encode_screen(1, 0, [], Image.new("L", (320, 223), 255))
        with self.assertRaises(ValueError):
            encode_screen(1, 0, [], Image.new("L", (321, 10), 255))
        with self.assertRaises(ValueError):
            encode_screen(1, 0, [], image, bpp=5)
        with self.assertRaises(ValueError):
            encode_screen(1, 0, [ScreenKey("1", ACTION_CLOSE), ScreenKey("1", ACTION_CLOSE)], image)
        with self.assertRaises(ValueError):
            encode_screen(1, 0, [("1", 0, b"")], image)
        many = [ScreenKey(chr(0x21 + i), ACTION_CLOSE) for i in range(65)]
        with self.assertRaises(ValueError):
            encode_screen(1, 0, many, image)
        self.assertEqual(len(decode_screen(encode_screen(1, 0, many[:64], image))[2]), 64)

    def test_decode_rejects_corruption(self):
        payload = encode_screen(9, 1, self.keys(), level_image(64, 20, 4, seed=3))
        with self.assertRaises(ValueError):
            decode_screen(payload[:9])
        with self.assertRaises(ValueError):
            decode_screen(payload[:14])
        with self.assertRaises(ValueError):
            decode_screen(payload[:-1])
        for offset, value in ((0, 0), (2, 65), (3, 3), (8, 9), (9, 1), (11, 9)):
            broken = bytearray(payload)
            broken[offset] = value
            with self.assertRaises(ValueError):
                decode_screen(bytes(broken))
        broken = bytearray(payload)
        broken[6:8] = (223).to_bytes(2, "big")
        with self.assertRaises(ValueError):
            decode_screen(bytes(broken))

    def test_module_constants_match_the_specification(self):
        self.assertEqual(imagecodec.BLOCK_HEADER.size, 12)
        self.assertEqual(imagecodec.SCREEN_HEADER.size, 10)
        self.assertEqual(
            (ACTION_CLOSE, ACTION_GOTO, ACTION_INSERT, ACTION_COMMAND, ACTION_SEND, ACTION_SEND_KEEP),
            (0, 1, 2, 3, 4, 5),
        )
        self.assertEqual((imagecodec.KIND_ASSISTANT, imagecodec.KIND_USER, imagecodec.KIND_INFO), (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
