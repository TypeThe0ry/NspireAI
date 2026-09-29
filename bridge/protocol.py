"""Wire format shared by the Ndless TI_NN service and the Mac helper."""
from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import Iterable

MAGIC = b"NSAI"
VERSION = 1
HEADER = struct.Struct(">4sBBIHI")  # magic, version, opcode, request, conversation, payload length
# TI's NavNet API documents a maximum service payload of 254 bytes.  Keep
# enough room for the NSAI header and the NavNet framing rather than relying
# on the larger buffers exposed by one particular CX II host connector.
MAX_PAYLOAD = 224
# Rendered image blocks and framebuffer dumps are far larger than text.
MAX_MESSAGE_PAYLOAD = 1024 * 1024
OP_PING, OP_PONG = 1, 2
OP_REQUEST, OP_RESPONSE, OP_ERROR = 3, 4, 5
OP_CANCEL, OP_NEW = 6, 7
OP_FRAGMENT = 8
# Protocol 2 (thin-terminal page): see src/page/page.c.
OP_HELLO = 9      # page -> host: "v=2;w=320;h=240;bpp=4;max=..;n=..;sid=..;mv=.."
OP_BLOCK = 10     # host -> page: rendered image block (bridge/imagecodec.py)
OP_SCREEN = 11    # host -> page: overlay screen with a key table
OP_ACTION = 12    # page -> host: action string chosen on a screen
OP_STATE = 13     # host -> page: "s=<session>;t=<title>;mv=<menu version>"
OP_DUMP_REQ = 14  # host -> page: send the framebuffer
OP_DUMP = 15      # page -> host: u16 w, u16 h, u8 format 1, runs {u8 n, u16 rgb565}
OP_INJECT = 16    # host -> page: key events for unattended tests
OP_CLEAR = 17     # host -> page: drop all history blocks
OP_PREVIEW_REQ = 18  # page -> host: input text to typeset; request id = input revision
OP_PREVIEW = 19   # host -> page: typeset input (BLOCK layout), empty = nothing to show
OP_IME_REQ = 20   # page -> host: u8 candidate page, then pinyin letters; id = revision
OP_IME = 21       # host -> page: u8 n, u8 flags, u8 page, u8 0, n x {u8 consumed, u8 len, text}, BLOCK image
OP_IME_PICK = 22  # page -> host: b"letters\ttext\tletters left" chosen by the user (learning)
FRAGMENT_HEADER = struct.Struct(">BBII")  # original opcode, reserved, total, offset

def encode(opcode: int, request_id: int, conversation_id: int, payload: bytes) -> bytes:
    if not 0 <= opcode <= 255 or not 0 <= request_id <= 0xFFFFFFFF or not 0 <= conversation_id <= 0xFFFF:
        raise ValueError("frame field out of range")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return HEADER.pack(MAGIC, VERSION, opcode, request_id, conversation_id, len(payload)) + payload


def fragment(opcode: int, request_id: int, conversation_id: int, payload: bytes) -> Iterable[bytes]:
    """Yield one frame or ordered OP_FRAGMENT frames for a logical message."""
    if len(payload) > MAX_MESSAGE_PAYLOAD:
        raise ValueError("message too large")
    if len(payload) <= MAX_PAYLOAD:
        yield encode(opcode, request_id, conversation_id, payload)
        return
    chunk_size = MAX_PAYLOAD - FRAGMENT_HEADER.size
    offset = 0
    while offset < len(payload):
        chunk = payload[offset:offset + chunk_size]
        fragment_payload = FRAGMENT_HEADER.pack(opcode, 0, len(payload), offset) + chunk
        yield encode(OP_FRAGMENT, request_id, conversation_id, fragment_payload)
        offset += len(chunk)

def decode(frame: bytes) -> tuple[int, int, int, bytes]:
    if len(frame) < HEADER.size:
        raise ValueError("short frame")
    magic, version, opcode, request_id, conversation_id, length = HEADER.unpack_from(frame)
    if magic != MAGIC or version != VERSION:
        raise ValueError("invalid frame header")
    payload = frame[HEADER.size:]
    if length != len(payload) or length > MAX_PAYLOAD:
        raise ValueError("invalid payload length")
    return opcode, request_id, conversation_id, payload


@dataclass
class FragmentBuffer:
    opcode: int
    total: int
    data: bytearray
    next_offset: int = 0

    def add(self, payload: bytes) -> bytes | None:
        if len(payload) < FRAGMENT_HEADER.size:
            raise ValueError("short fragment")
        opcode, reserved, total, offset = FRAGMENT_HEADER.unpack_from(payload)
        if reserved != 0 or opcode != self.opcode or total != self.total or offset != self.next_offset:
            raise ValueError("fragment sequence mismatch")
        chunk = payload[FRAGMENT_HEADER.size:]
        self.data.extend(chunk)
        self.next_offset += len(chunk)
        if self.next_offset > self.total:
            raise ValueError("fragment data exceeds message length")
        return bytes(self.data) if self.next_offset == self.total else None


class FragmentReassembler:
    def __init__(self):
        self._buffers: dict[tuple[int, int], FragmentBuffer] = {}

    def add(self, conversation_id: int, request_id: int, payload: bytes) -> tuple[int, bytes] | None:
        if len(payload) < FRAGMENT_HEADER.size:
            raise ValueError("short fragment")
        opcode, reserved, total, offset = FRAGMENT_HEADER.unpack_from(payload)
        if reserved != 0 or total > MAX_MESSAGE_PAYLOAD or offset < 0:
            raise ValueError("invalid fragment header")
        key = (conversation_id, request_id)
        if offset == 0:
            self._buffers[key] = FragmentBuffer(opcode, total, bytearray())
        buffer = self._buffers.get(key)
        if buffer is None:
            raise ValueError("fragment without start")
        try:
            complete = buffer.add(payload)
        except Exception:
            self._buffers.pop(key, None)
            raise
        if complete is None:
            return None
        self._buffers.pop(key, None)
        return buffer.opcode, complete
