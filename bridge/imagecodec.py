"""Image payloads shared by the bridge and the calculator page.

The calculator only has a tiny ASCII font, so the bridge renders answers and
menus to grayscale images and ships them as BLOCK and SCREEN payloads.  The C
decoder on the handheld is written against the byte layouts documented here;
change them only together with it.

Pixel model
    Grayscale, ``bpp`` in {1, 2, 4}.  Value 0 is full ink (black) and the
    maximum value ``2**bpp - 1`` is paper (white).  Rows are packed MSB-first
    (the first pixel lives in the high bits of the first byte) and every row
    is padded with zero bits to a whole byte::

        row_bytes = ceil(width * bpp / 8)
        raw       = height rows, concatenated

PackBits (over the whole raw byte string, not per row), control byte ``c``:
    0..127    copy the next ``c + 1`` literal bytes
    129..255  repeat the next byte ``257 - c`` times (2..128 repeats)
    128       no-op (never emitted)

BLOCK payload (big-endian)::

    0   u8   kind       0 = assistant turn, 1 = user turn, 2 = system/info
    1   u8   bpp        1, 2 or 4
    2   u16  width      1..320
    4   u16  height     1..65535
    6   u8   encoding   0 = raw, 1 = PackBits
    7   u8   reserved   0 (the page host uses bit0 as "continues the previous
                        block, no gap above"; see BLOCK_FLAG_CONTINUATION)
    8   u32  block_id
    12  data            raw rows, or PackBits of the raw rows

SCREEN payload (big-endian)::

    0   u8   screen_id  1..255
    1   u8   flags      bit0 = show immediately when received
    2   u8   nkeys      0..64
    3   u8   bpp
    4   u16  width      1..320
    6   u16  height     1..222
    8   u8   encoding
    9   u8   reserved   0
    10  key table: nkeys * { u8 key_char; u8 action_type; u8 arg_len; arg }
    ..  image data      raw rows, or PackBits of the raw rows
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from PIL import Image

BPP_VALUES = (1, 2, 4)
MAX_WIDTH = 320
MAX_BLOCK_HEIGHT = 0xFFFF
MAX_SCREEN_HEIGHT = 222
MAX_SCREEN_KEYS = 64

ENCODING_RAW = 0
ENCODING_PACKBITS = 1

KIND_ASSISTANT = 0
KIND_USER = 1
KIND_INFO = 2

FLAG_SHOW = 0x01                 # SCREEN flags bit0: show immediately when received
BLOCK_FLAG_CONTINUATION = 0x01   # BLOCK byte 7 bit0: strip joins the previous block

ACTION_CLOSE = 0        # close the overlay
ACTION_GOTO = 1         # arg = 1 byte screen id
ACTION_INSERT = 2       # arg = ASCII text inserted into the input line, then close
ACTION_COMMAND = 3      # arg = ASCII "id|label" (label <= 12 chars), then close
ACTION_SEND = 4         # arg = ASCII action string sent to the host, then close
ACTION_SEND_KEEP = 5    # like ACTION_SEND, but the overlay stays open
ACTIONS = (ACTION_CLOSE, ACTION_GOTO, ACTION_INSERT, ACTION_COMMAND, ACTION_SEND, ACTION_SEND_KEEP)
MAX_COMMAND_LABEL = 12

BLOCK_HEADER = struct.Struct(">BBHHBBI")    # kind, bpp, width, height, encoding, reserved, block id
SCREEN_HEADER = struct.Struct(">BBBBHHBB")  # id, flags, nkeys, bpp, width, height, encoding, reserved


# --------------------------------------------------------------------------
# PackBits
# --------------------------------------------------------------------------

def packbits_encode(data: bytes) -> bytes:
    """PackBits-compress ``data``.  The no-op control byte 128 is never emitted."""
    data = bytes(data)
    out = bytearray()
    n = len(data)
    literal_start = 0   # start of the pending literal bytes
    i = 0

    def flush_literals(end: int) -> None:
        start = literal_start
        while start < end:
            count = min(128, end - start)
            out.append(count - 1)
            out.extend(data[start:start + count])
            start += count

    while i < n:
        byte = data[i]
        run = 1
        while run < 128 and i + run < n and data[i + run] == byte:
            run += 1
        # A run of two only pays off when it does not split a literal run
        # (it would cost an extra control byte there).
        if run >= 3 or (run == 2 and literal_start == i):
            flush_literals(i)
            out.append(257 - run)
            out.append(byte)
            i += run
            literal_start = i
        else:
            i += run
    flush_literals(n)
    return bytes(out)


def packbits_decode(data: bytes, expected_len: int) -> bytes:
    """Inverse of :func:`packbits_encode`.

    Raises ValueError when the stream is truncated, produces more than
    ``expected_len`` bytes (overrun) or fewer (underrun).
    """
    if expected_len < 0:
        raise ValueError("expected length must not be negative")
    data = bytes(data)
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        control = data[i]
        i += 1
        if control == 128:
            continue
        if control < 128:
            count = control + 1
            if i + count > n:
                raise ValueError("PackBits literal run is truncated")
            out += data[i:i + count]
            i += count
        else:
            if i >= n:
                raise ValueError("PackBits repeat run is truncated")
            out += data[i:i + 1] * (257 - control)
            i += 1
        if len(out) > expected_len:
            raise ValueError("PackBits data overruns the expected length")
    if len(out) != expected_len:
        raise ValueError("PackBits data underruns the expected length")
    return bytes(out)


# --------------------------------------------------------------------------
# Pixel packing
# --------------------------------------------------------------------------

def _check_bpp(bpp: int) -> int:
    if bpp not in BPP_VALUES:
        raise ValueError(f"bpp must be one of {BPP_VALUES}, not {bpp!r}")
    return bpp


def row_bytes(width: int, bpp: int) -> int:
    """Bytes per packed row: ceil(width * bpp / 8)."""
    return (width * _check_bpp(bpp) + 7) // 8


def _repeated(byte: int, count: int) -> int:
    return int.from_bytes(bytes([byte]) * count, "big")


def quantize(image: Image.Image, bpp: int) -> bytes:
    """Quantize ``image`` to ``bpp`` bits per pixel and pack its rows.

    The image is converted to mode "L" (0 = black, 255 = white); each pixel
    becomes ``round(L * max / 255)`` with ``max = 2**bpp - 1``.
    """
    _check_bpp(bpp)
    gray = image if image.mode == "L" else image.convert("L")
    width, height = gray.size
    if width < 1 or height < 1:
        raise ValueError("image must not be empty")
    top = (1 << bpp) - 1
    # Integer form of round(L * top / 255); an exact .5 cannot occur.
    levels = gray.point([(value * top * 2 + 255) // 510 for value in range(256)])
    per_byte = 8 // bpp
    padded_width = -(-width // per_byte) * per_byte
    if padded_width != width:
        padded = Image.new("L", (padded_width, height), 0)
        padded.paste(levels, (0, 0))
        levels = padded
    pixels = levels.tobytes()
    # Combine `per_byte` pixel planes with big-integer shifts: this stays in C
    # for the whole image instead of looping over pixels in Python.
    count = len(pixels) // per_byte
    packed = 0
    for plane in range(per_byte):
        shift = 8 - bpp * (plane + 1)
        packed |= int.from_bytes(pixels[plane::per_byte], "big") << shift
    return packed.to_bytes(count, "big")


def unpack(raw: bytes, width: int, height: int, bpp: int) -> Image.Image:
    """Inverse of :func:`quantize`: packed rows to a mode "L" image."""
    _check_bpp(bpp)
    if width < 1 or height < 1:
        raise ValueError("image must not be empty")
    stride = row_bytes(width, bpp)
    if len(raw) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of pixel data, got {len(raw)}")
    top = (1 << bpp) - 1
    per_byte = 8 // bpp
    count = len(raw)
    packed = int.from_bytes(raw, "big")
    mask = _repeated(top, count)
    pixels = bytearray(count * per_byte)
    for plane in range(per_byte):
        shift = 8 - bpp * (plane + 1)
        pixels[plane::per_byte] = ((packed >> shift) & mask).to_bytes(count, "big")
    levels = Image.frombytes("L", (stride * per_byte, height), bytes(pixels))
    if levels.width != width:
        levels = levels.crop((0, 0, width, height))
    return levels.point([min(255, value * 255 // top) for value in range(256)])


def _encode_pixels(image: Image.Image, bpp: int) -> tuple[int, bytes]:
    raw = quantize(image, bpp)
    packed = packbits_encode(raw)
    if len(packed) < len(raw):
        return ENCODING_PACKBITS, packed
    return ENCODING_RAW, raw


def _decode_pixels(data: bytes, encoding: int, width: int, height: int, bpp: int) -> Image.Image:
    expected = row_bytes(width, bpp) * height
    if encoding == ENCODING_RAW:
        if len(data) != expected:
            raise ValueError(f"expected {expected} bytes of raw pixel data, got {len(data)}")
        raw = bytes(data)
    elif encoding == ENCODING_PACKBITS:
        raw = packbits_decode(data, expected)
    else:
        raise ValueError(f"unknown encoding {encoding}")
    return unpack(raw, width, height, bpp)


def _check_size(image: Image.Image, max_height: int) -> tuple[int, int]:
    width, height = image.size
    if not 1 <= width <= MAX_WIDTH:
        raise ValueError(f"image width {width} is outside 1..{MAX_WIDTH}")
    if not 1 <= height <= max_height:
        raise ValueError(f"image height {height} is outside 1..{max_height}")
    return width, height


# --------------------------------------------------------------------------
# BLOCK
# --------------------------------------------------------------------------

def encode_block(kind: int, image: Image.Image, block_id: int, bpp: int = 4, flags: int = 0) -> bytes:
    """Build a BLOCK payload; PackBits is used when it is smaller than raw.

    ``flags`` is stored in the reserved byte (offset 7) and is 0 by default.
    """
    if not 0 <= kind <= 0xFF:
        raise ValueError("block kind out of range")
    if not 0 <= block_id <= 0xFFFFFFFF:
        raise ValueError("block id out of range")
    if not 0 <= flags <= 0xFF:
        raise ValueError("block flags out of range")
    _check_bpp(bpp)
    width, height = _check_size(image, MAX_BLOCK_HEIGHT)
    encoding, data = _encode_pixels(image, bpp)
    return BLOCK_HEADER.pack(kind, bpp, width, height, encoding, flags, block_id) + data


def block_flags(payload: bytes) -> int:
    """The reserved/flags byte (offset 7) of a BLOCK payload."""
    if len(payload) < BLOCK_HEADER.size:
        raise ValueError("short block payload")
    return payload[7]


def decode_block(payload: bytes) -> tuple[int, int, int, int, int, Image.Image]:
    """Return ``(kind, bpp, width, height, block_id, image)``.

    The reserved byte is not checked, because the page host stores flags in
    it; read it with :func:`block_flags`.
    """
    if len(payload) < BLOCK_HEADER.size:
        raise ValueError("short block payload")
    kind, bpp, width, height, encoding, _flags, block_id = BLOCK_HEADER.unpack_from(payload)
    _check_bpp(bpp)
    if not 1 <= width <= MAX_WIDTH or height < 1:
        raise ValueError("invalid block dimensions")
    image = _decode_pixels(payload[BLOCK_HEADER.size:], encoding, width, height, bpp)
    return kind, bpp, width, height, block_id, image


def _is_blank_row(image: Image.Image, y: int) -> bool:
    return image.crop((0, y, image.width, y + 1)).getextrema()[0] == 255


def split_image(image: Image.Image, max_rows: int, search: int = 24) -> Iterator[Image.Image]:
    """Cut a tall image into strips of at most ``max_rows`` rows.

    Cuts prefer an all-white row within ``search`` rows above the limit, so a
    line of text is not split across two strips when there is a gap nearby.
    """
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    gray = image if image.mode == "L" else image.convert("L")
    top = 0
    while top < gray.height:
        bottom = min(gray.height, top + max_rows)
        if bottom < gray.height:
            for candidate in range(bottom, max(top + 1, bottom - search), -1):
                if _is_blank_row(gray, candidate - 1):
                    bottom = candidate
                    break
        yield gray.crop((0, top, gray.width, bottom))
        top = bottom


def encode_blocks(
    kind: int,
    image: Image.Image,
    first_block_id: int,
    bpp: int = 4,
    max_payload: int = 60 * 1024,
    max_rows: int = 480,
    same_id: bool = False,
) -> list[bytes]:
    """Encode ``image`` as consecutive BLOCK payloads of at most ``max_payload`` bytes.

    Block ids count up from ``first_block_id``, or all strips share it when
    ``same_id`` is set.  Every strip after the first carries
    BLOCK_FLAG_CONTINUATION.  Strips are halved until their payload fits.
    """
    if max_payload <= BLOCK_HEADER.size + 1:
        raise ValueError("max_payload is too small")
    pending = list(split_image(image, max_rows))
    pending.reverse()
    payloads: list[bytes] = []
    while pending:
        strip = pending.pop()
        block_id = first_block_id if same_id else first_block_id + len(payloads)
        flags = BLOCK_FLAG_CONTINUATION if payloads else 0
        payload = encode_block(kind, strip, block_id, bpp, flags)
        if len(payload) > max_payload:
            if strip.height == 1:
                raise ValueError("a single row does not fit max_payload")
            halves = list(split_image(strip, (strip.height + 1) // 2))
            pending.extend(reversed(halves))
            continue
        payloads.append(payload)
    return payloads


# --------------------------------------------------------------------------
# SCREEN
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenKey:
    """One entry of a SCREEN key table."""

    key: str            # the ASCII character the key produces
    action: int         # one of the ACTION_* values
    arg: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or len(self.key) != 1 or not 0 < ord(self.key) < 0x80:
            raise ValueError(f"screen key must be one ASCII character, not {self.key!r}")
        if self.action not in ACTIONS:
            raise ValueError(f"unknown screen action {self.action!r}")
        if isinstance(self.arg, str):
            try:
                object.__setattr__(self, "arg", self.arg.encode("ascii"))
            except UnicodeEncodeError as exc:
                raise ValueError("screen key argument must be ASCII") from exc
        elif not isinstance(self.arg, (bytes, bytearray)):
            raise ValueError("screen key argument must be bytes")
        else:
            object.__setattr__(self, "arg", bytes(self.arg))
        arg = self.arg
        if len(arg) > 255:
            raise ValueError("screen key argument is longer than 255 bytes")
        if self.action == ACTION_GOTO:
            if len(arg) != 1 or arg[0] == 0:
                raise ValueError("goto needs a one byte screen id in 1..255")
            return
        if any(byte < 0x20 or byte > 0x7E for byte in arg):
            raise ValueError("screen key argument must be printable ASCII")
        if self.action == ACTION_COMMAND:
            command_id, separator, label = arg.partition(b"|")
            if not separator or not command_id or len(label) > MAX_COMMAND_LABEL:
                raise ValueError('command argument must be "id|label" with a label of at most 12 characters')

    def encode(self) -> bytes:
        return bytes([ord(self.key), self.action, len(self.arg)]) + self.arg


def encode_screen(
    screen_id: int,
    flags: int,
    keys: Sequence[ScreenKey] | Iterable[ScreenKey],
    image: Image.Image,
    bpp: int = 4,
) -> bytes:
    """Build a SCREEN payload; PackBits is used when it is smaller than raw."""
    if not 1 <= screen_id <= 0xFF:
        raise ValueError("screen id must be in 1..255")
    if not 0 <= flags <= 0xFF:
        raise ValueError("screen flags out of range")
    keys = list(keys)
    if len(keys) > MAX_SCREEN_KEYS:
        raise ValueError(f"a screen has at most {MAX_SCREEN_KEYS} keys")
    seen: set[str] = set()
    for key in keys:
        if not isinstance(key, ScreenKey):
            raise ValueError("keys must be ScreenKey instances")
        if key.key in seen:
            raise ValueError(f"duplicate screen key {key.key!r}")
        seen.add(key.key)
    _check_bpp(bpp)
    width, height = _check_size(image, MAX_SCREEN_HEIGHT)
    encoding, data = _encode_pixels(image, bpp)
    header = SCREEN_HEADER.pack(screen_id, flags, len(keys), bpp, width, height, encoding, 0)
    return header + b"".join(key.encode() for key in keys) + data


def decode_screen(payload: bytes) -> tuple[int, int, list[ScreenKey], int, int, int, Image.Image]:
    """Return ``(screen_id, flags, keys, bpp, width, height, image)``."""
    if len(payload) < SCREEN_HEADER.size:
        raise ValueError("short screen payload")
    screen_id, flags, nkeys, bpp, width, height, encoding, reserved = SCREEN_HEADER.unpack_from(payload)
    if reserved != 0:
        raise ValueError("reserved screen byte is not zero")
    if screen_id == 0:
        raise ValueError("screen id must be in 1..255")
    if nkeys > MAX_SCREEN_KEYS:
        raise ValueError(f"a screen has at most {MAX_SCREEN_KEYS} keys")
    _check_bpp(bpp)
    if not 1 <= width <= MAX_WIDTH or not 1 <= height <= MAX_SCREEN_HEIGHT:
        raise ValueError("invalid screen dimensions")
    offset = SCREEN_HEADER.size
    keys: list[ScreenKey] = []
    for _ in range(nkeys):
        if offset + 3 > len(payload):
            raise ValueError("screen key table is truncated")
        key_char, action, arg_len = payload[offset:offset + 3]
        offset += 3
        if offset + arg_len > len(payload):
            raise ValueError("screen key argument is truncated")
        if not 0 < key_char < 0x80:
            raise ValueError("screen key is not ASCII")
        keys.append(ScreenKey(chr(key_char), action, bytes(payload[offset:offset + arg_len])))
        offset += arg_len
    image = _decode_pixels(payload[offset:], encoding, width, height, bpp)
    return screen_id, flags, keys, bpp, width, height, image
