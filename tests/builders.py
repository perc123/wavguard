import struct
from collections.abc import Sequence

PCM = 1
FLOAT = 3
EXTENSIBLE = 0xFFFE
GUID_SUFFIX = bytes.fromhex("000000001000800000aa00389b71")

# 24-bit stereo: 6-byte frames, so a 4-byte cut misaligns and block_align 8 is wrong (SPEC §7).
CHANNELS = 2
BITS = 24
RATE = 48000
FRAME_SIZE = CHANNELS * BITS // 8
N_FRAMES = 10


def chunk(id: bytes, payload: bytes, *, size: int | None = None, pad: bool = True) -> bytes:
    declared = len(payload) if size is None else size
    pad_byte = b"\x00" if pad and len(payload) % 2 else b""
    return id + struct.pack("<I", declared) + payload + pad_byte


def fmt_payload(
    tag: int = PCM,
    channels: int = CHANNELS,
    rate: int = RATE,
    bits: int = BITS,
    *,
    block_align: int | None = None,
    extensible: bool = False,
) -> bytes:
    frame_size = channels * bits // 8
    align = frame_size if block_align is None else block_align
    header_tag = EXTENSIBLE if extensible else tag
    payload = struct.pack("<HHIIHH", header_tag, channels, rate, rate * frame_size, align, bits)
    if extensible:
        channel_mask = (1 << channels) - 1
        payload += struct.pack("<HHIH", 22, bits, channel_mask, tag) + GUID_SUFFIX
    return payload


def frames(n: int = N_FRAMES, frame_size: int = FRAME_SIZE) -> bytes:
    # Bytes 0x80-0xFF are never printable ASCII, so audio never looks like a chunk ID (SPEC §4.3).
    return bytes(0x80 + i % 0x80 for i in range(n * frame_size))


def wav(
    chunks: Sequence[bytes] | None = None,
    *,
    riff_size: int | None = None,
    magic: bytes = b"RIFF",
) -> bytes:
    if chunks is None:
        chunks = [chunk(b"fmt ", fmt_payload()), chunk(b"data", frames())]
    body = b"WAVE" + b"".join(chunks)
    size = len(body) if riff_size is None else riff_size
    return magic + struct.pack("<I", size) + body
