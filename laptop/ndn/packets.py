"""NDN packets and the PacketFactory.

Named Data Networking in one paragraph: nobody sends data "to an address".
A node asks for data by NAME with an *Interest* packet (e.g. "/qanoo/hello").
Whoever has data with that name answers with a *Data* packet carrying the
same name. Any node may cache Data and answer later Interests from its cache.

Wire format: a tiny TLV (Type-Length-Value) encoding modelled on real NDN,
with 1-byte lengths because a LoRa frame is at most 255 bytes.

Design patterns:
  * Factory Method: PacketFactory.decode() picks the right Packet subclass from
    the type byte; callers never call Interest(...) / Data(...) on raw bytes.
  * Open/Closed: a new packet type is added by registering a decoder, without
    changing the existing ones.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

MAX_FRAME = 255  # largest LoRa payload of the SX1278

# TLV type numbers (same numbers as the real NDN spec)
T_INTEREST = 0x05
T_DATA = 0x06
T_NAME = 0x07
T_NONCE = 0x0A
T_CONTENT = 0x15
T_FINAL_BLOCK = 0x1A


class PacketError(ValueError):
    pass


def _tlv(t: int, value: bytes) -> bytes:
    if len(value) > 255:
        raise PacketError("TLV value too long (%d bytes)" % len(value))
    return bytes([t, len(value)]) + value


def _parse_tlvs(buf: bytes) -> dict[int, bytes]:
    out, i = {}, 0
    while i < len(buf):
        if i + 2 > len(buf):
            raise PacketError("truncated TLV")
        t, n = buf[i], buf[i + 1]
        if i + 2 + n > len(buf):
            raise PacketError("truncated TLV value")
        out[t] = buf[i + 2:i + 2 + n]
        i += 2 + n
    return out


class Packet(ABC):
    """Common base: every NDN packet has a name and can be encoded."""

    def __init__(self, name: str):
        if not name.startswith("/"):
            raise PacketError("NDN names start with '/': %r" % name)
        self.name = name

    @abstractmethod
    def encode(self) -> bytes: ...

    def _check(self, raw: bytes) -> bytes:
        if len(raw) > MAX_FRAME:
            raise PacketError("packet is %d bytes, LoRa limit is %d" % (len(raw), MAX_FRAME))
        return raw


class Interest(Packet):
    """'Please give me the data called <name>.'"""

    def __init__(self, name: str, nonce: bytes | None = None):
        super().__init__(name)
        self.nonce = nonce or os.urandom(4)

    def encode(self) -> bytes:
        body = _tlv(T_NAME, self.name.encode()) + _tlv(T_NONCE, self.nonce)
        return self._check(_tlv(T_INTEREST, body))

    def __repr__(self):
        return "Interest(%s)" % self.name


class Data(Packet):
    """'Here is the data called <name>.' final_block = index of the last segment."""

    def __init__(self, name: str, content: bytes, final_block: int | None = None):
        super().__init__(name)
        self.content = content
        self.final_block = final_block

    def encode(self) -> bytes:
        body = _tlv(T_NAME, self.name.encode()) + _tlv(T_CONTENT, self.content)
        if self.final_block is not None:
            body += _tlv(T_FINAL_BLOCK, bytes([self.final_block]))
        return self._check(_tlv(T_DATA, body))

    def __repr__(self):
        return "Data(%s, %d B)" % (self.name, len(self.content))


class PacketFactory:
    """Factory Method: the only place that turns raw bytes into Packet objects."""

    def __init__(self):
        self._decoders = {T_INTEREST: self._decode_interest, T_DATA: self._decode_data}

    # --- creation ---
    def interest(self, name: str) -> Interest:
        return Interest(name)

    def data(self, name: str, content: bytes, final_block: int | None = None) -> Data:
        return Data(name, content, final_block)

    def decode(self, raw: bytes) -> Packet:
        if len(raw) < 2:
            raise PacketError("frame too short")
        decoder = self._decoders.get(raw[0])
        if decoder is None:
            raise PacketError("unknown packet type 0x%02x" % raw[0])
        if 2 + raw[1] > len(raw):
            raise PacketError("truncated packet")
        return decoder(_parse_tlvs(raw[2:2 + raw[1]]))

    # --- decoders ---
    @staticmethod
    def _decode_interest(f: dict[int, bytes]) -> Interest:
        if T_NAME not in f:
            raise PacketError("Interest without name")
        return Interest(f[T_NAME].decode(), f.get(T_NONCE))

    @staticmethod
    def _decode_data(f: dict[int, bytes]) -> Data:
        if T_NAME not in f:
            raise PacketError("Data without name")
        final = f[T_FINAL_BLOCK][0] if T_FINAL_BLOCK in f else None
        return Data(f[T_NAME].decode(), f.get(T_CONTENT, b""), final)


# ---------- segmentation (big content -> several Data packets) ----------

SEGMENT_MARK = "/s"


def segment_name(base: str, index: int) -> str:
    return "%s%s%d" % (base, SEGMENT_MARK, index)


def split_segment_name(name: str) -> tuple[str, int] | None:
    """'/qanoo/story/s3' -> ('/qanoo/story', 3); returns None if no segment suffix."""
    base, sep, idx = name.rpartition(SEGMENT_MARK)
    if sep and idx.isdigit() and base:
        return base, int(idx)
    return None


class Segmenter:
    """Splits content so each Data packet fits into one LoRa frame."""

    OVERHEAD = 2 + 2 + 2 + 3  # outer TLV + name TLV + content TLV + final-block TLV

    def __init__(self, factory: PacketFactory):
        self.factory = factory

    def split(self, base: str, content: bytes) -> list[Data]:
        room = MAX_FRAME - self.OVERHEAD - len(segment_name(base, 255).encode())
        if room < 16:
            raise PacketError("name too long for LoRa: %r" % base)
        chunks = [content[i:i + room] for i in range(0, len(content), room)] or [b""]
        if len(chunks) > 256:
            raise PacketError("content too large (%d bytes)" % len(content))
        last = len(chunks) - 1
        return [self.factory.data(segment_name(base, i), c, last) for i, c in enumerate(chunks)]
