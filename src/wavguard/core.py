import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import BinaryIO

from wavguard.model import Code, Finding, Report

HEADER_SIZE = 12
UNSUPPORTED_MAGICS = frozenset({b"RF64", b"BW64", b"RIFX", b"riff"})
SIZE_UNKNOWN = 0xFFFFFFFF
CHUNK_HEADER_SIZE = 8
MAX_CHUNKS = 1024
PAD_WINDOW_SIZE = 5
MIN_FMT_SIZE = 16
MIN_EXTENSIBLE_FMT_SIZE = 40
MAX_FMT_SIZE = 1024
PCM_TAG = 1
FLOAT_TAG = 3
EXTENSIBLE_TAG = 0xFFFE
SUBFORMAT_SUFFIX = bytes.fromhex("00 00 00 00 10 00 80 00 00 AA 00 38 9B 71")

ReadAt = Callable[[int, int], bytes]


@dataclass(frozen=True)
class _ChunkRef:
    id: bytes
    header_offset: int
    payload_offset: int
    size: int


@dataclass(frozen=True)
class _ScanResult:
    fmt: _ChunkRef | None
    data: _ChunkRef | None
    findings: tuple[Finding, ...]
    limit_hit: bool


@dataclass(frozen=True)
class _FmtInfo:
    tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    block_align: int
    frame_size: int


@dataclass(frozen=True)
class _FmtResult:
    findings: tuple[Finding, ...]
    info: _FmtInfo | None


def validate(source: str | os.PathLike[str] | BinaryIO, *, strict: bool = False) -> Report:
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as f:
            return _validate_open(f, strict)
    if not (hasattr(source, "read") and hasattr(source, "seekable")):
        raise TypeError(f"expected a path or a binary file object, got {type(source).__name__}")
    if not source.seekable():
        raise ValueError("file object must be seekable")
    if not isinstance(source.read(0), bytes):
        raise TypeError("file object must be opened in binary mode")
    return _validate_open(source, strict)


def _validate_open(f: BinaryIO, strict: bool) -> Report:
    file_size = _file_size(f)
    head = _read_at(f, 0, HEADER_SIZE)
    findings, keep_going = _check_header(head, file_size)
    if not keep_going:
        return Report(findings=tuple(findings), strict=strict)

    read_at = partial(_read_at, f)
    scan = _scan_chunks(read_at, file_size)
    findings.extend(scan.findings)

    info = None
    if scan.fmt is not None:
        fmt = _check_fmt(read_at, scan.fmt, file_size)
        findings.extend(fmt.findings)
        info = fmt.info
    frame_size = info.frame_size if info else None

    frames_declared = complete_frames = None
    if scan.data is not None:
        findings.extend(_check_data(scan.data, file_size, frame_size))
        if frame_size:
            if scan.data.size != SIZE_UNKNOWN:
                frames_declared = scan.data.size // frame_size
            complete_frames = _usable_bytes(scan.data, file_size) // frame_size

    if not scan.limit_hit:
        if scan.fmt is None:
            findings.append(Finding(Code.NO_FMT_CHUNK, "no fmt chunk found", None))
        if scan.data is None:
            findings.append(Finding(Code.NO_DATA_CHUNK, "no data chunk found", None))

    if info is None:
        return Report(findings=tuple(findings), strict=strict)
    return Report(
        findings=tuple(findings),
        strict=strict,
        channels=info.channels,
        bits_per_sample=info.bits_per_sample,
        sample_rate=info.sample_rate,
        frame_size=info.frame_size,
        frames_declared=frames_declared,
        complete_frames=complete_frames,
    )


def _check_header(head: bytes, file_size: int) -> tuple[list[Finding], bool]:
    if file_size < HEADER_SIZE:
        message = f"file is {_plural(file_size, 'byte')}, shorter than the {HEADER_SIZE}-byte RIFF header"
        return [Finding(Code.NOT_RIFF_WAVE, message, 0)], False

    magic, form = head[0:4], head[8:12]
    if magic in UNSUPPORTED_MAGICS:
        message = f"{magic.decode('ascii')} files are not supported in v0.1"
        return [Finding(Code.UNSUPPORTED_FORMAT, message, 0)], False
    if magic != b"RIFF" or form != b"WAVE":
        message = f"expected RIFF/WAVE header, found {magic!r} with form type {form!r}"
        return [Finding(Code.NOT_RIFF_WAVE, message, 0)], False

    (riff_size,) = struct.unpack_from("<I", head, 4)
    findings = []
    if riff_size != SIZE_UNKNOWN:
        riff_end = 8 + riff_size
        if riff_end > file_size:
            message = (
                f"RIFF size {riff_size} needs {_plural(riff_end, 'byte')}, "
                f"but the file is {_plural(file_size, 'byte')}"
            )
            findings.append(Finding(Code.RIFF_SIZE_EXCEEDS_FILE, message, 4))
        elif file_size > riff_end:
            message = f"{_plural(file_size - riff_end, 'byte')} after the RIFF chunk, which ends at {riff_end}"
            findings.append(Finding(Code.TRAILING_BYTES, message, riff_end))
    return findings, True


def _scan_chunks(read_at: ReadAt, file_size: int) -> _ScanResult:
    # Terminates: every pass advances at least CHUNK_HEADER_SIZE bytes or stops, and MAX_CHUNKS caps the passes.
    offset = HEADER_SIZE
    count = 0
    fmt: _ChunkRef | None = None
    data: _ChunkRef | None = None
    findings: list[Finding] = []
    limit_hit = False

    while file_size - offset >= CHUNK_HEADER_SIZE:
        if count == MAX_CHUNKS:
            message = f"more than {MAX_CHUNKS} chunks; scanning stopped"
            findings.append(Finding(Code.LIMIT_EXCEEDED, message, offset))
            limit_hit = True
            break

        header = read_at(offset, CHUNK_HEADER_SIZE)
        chunk_id = header[:4]
        if not _plausible_id(chunk_id):
            break
        count += 1
        (size,) = struct.unpack_from("<I", header, 4)
        ref = _ChunkRef(chunk_id, offset, offset + CHUNK_HEADER_SIZE, size)

        if chunk_id == b"fmt " and fmt is None:
            fmt = ref
        elif chunk_id == b"data" and data is None:
            data = ref

        if ref is data and size == 0 and _zero_size_data_has_bytes(read_at, ref, file_size):
            message = "data size is 0, but the bytes after its header are audio, not a chunk header"
            findings.append(Finding(Code.DATA_SIZE_ZERO_WITH_DATA, message, ref.payload_offset))
            break
        if chunk_id == b"data" and size == SIZE_UNKNOWN:
            break
        if ref.payload_offset + size > file_size:
            break
        offset, pad_missing = _next_offset(ref, read_at, file_size)
        if pad_missing:
            q = ref.payload_offset + size
            message = f"chunk '{chunk_id.decode('ascii')}' has odd size {size} but no pad byte at {q}"
            findings.append(Finding(Code.ODD_CHUNK_PAD_MISSING, message, q))

    return _ScanResult(fmt, data, tuple(findings), limit_hit)


def _check_fmt(read_at: ReadAt, ref: _ChunkRef, file_size: int) -> _FmtResult:
    if ref.size > MAX_FMT_SIZE:
        message = f"fmt chunk declares {_plural(ref.size, 'byte')}, over the {MAX_FMT_SIZE}-byte limit"
        return _FmtResult((Finding(Code.LIMIT_EXCEEDED, message, ref.header_offset),), None)

    length = min(ref.size, file_size - ref.payload_offset)
    if length < MIN_FMT_SIZE:
        message = (
            f"fmt payload has {_plural(length, 'byte')} (declared {ref.size}), "
            f"fewer than the {MIN_FMT_SIZE} required"
        )
        return _FmtResult((Finding(Code.FMT_INVALID, message, ref.header_offset),), None)

    payload = read_at(ref.payload_offset, length)
    return _parse_fmt(payload, ref.header_offset, ref.payload_offset)


def _parse_fmt(payload: bytes, header_offset: int, payload_offset: int) -> _FmtResult:
    if len(payload) < MIN_FMT_SIZE:
        message = f"fmt payload has {_plural(len(payload), 'byte')}, fewer than the {MIN_FMT_SIZE} required"
        return _fmt_failure(Code.FMT_INVALID, message, header_offset)

    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", payload)

    if tag == EXTENSIBLE_TAG:
        if len(payload) < MIN_EXTENSIBLE_FMT_SIZE:
            message = (
                f"extensible fmt payload has {_plural(len(payload), 'byte')}, "
                f"fewer than the {MIN_EXTENSIBLE_FMT_SIZE} required"
            )
            return _fmt_failure(Code.FMT_INVALID, message, header_offset)
        subformat = payload[24:40]
        (tag,) = struct.unpack_from("<H", subformat)
        if subformat[2:] != SUBFORMAT_SUFFIX or tag not in (PCM_TAG, FLOAT_TAG):
            message = f"extensible SubFormat {subformat.hex()} is not PCM or IEEE float"
            return _fmt_failure(Code.UNSUPPORTED_FORMAT, message, payload_offset + 24)
    elif tag not in (PCM_TAG, FLOAT_TAG):
        message = f"format tag {tag:#06x} is not supported in v0.1"
        return _fmt_failure(Code.UNSUPPORTED_FORMAT, message, payload_offset)

    problems = []
    if channels == 0:
        problems.append("channels is 0")
    if rate == 0:
        problems.append("sample rate is 0")
    if block_align == 0:
        problems.append("block_align is 0")
    if bits == 0:
        problems.append("bits per sample is 0")
    elif bits % 8:
        problems.append(f"bits per sample {bits} is not a multiple of 8")
    if tag == FLOAT_TAG and bits != 0 and bits not in (32, 64):
        problems.append(f"float with {bits} bits per sample, expected 32 or 64")
    if problems:
        return _fmt_failure(Code.FMT_INVALID, "invalid fmt: " + "; ".join(problems), header_offset)

    frame_size = channels * bits // 8
    findings = []
    if block_align != frame_size:
        message = (
            f"block_align is {block_align}, but {_plural(channels, 'channel')} "
            f"x {_plural(bits // 8, 'byte')} is {frame_size}"
        )
        findings.append(Finding(Code.BLOCK_ALIGN_MISMATCH, message, payload_offset + 12))
    info = _FmtInfo(tag, channels, rate, bits, block_align, frame_size)
    return _FmtResult(tuple(findings), info)


def _check_data(ref: _ChunkRef, file_size: int, frame_size: int | None) -> list[Finding]:
    findings = []
    available = file_size - ref.payload_offset

    if ref.size == SIZE_UNKNOWN:
        message = f"data size is {SIZE_UNKNOWN:#x} (unknown); assuming data runs to the end of the file"
        findings.append(Finding(Code.DATA_SIZE_UNKNOWN, message, ref.header_offset))
    elif ref.size > available:
        message = f"data size {ref.size} exceeds the {_plural(available, 'byte')} present"
        findings.append(Finding(Code.DATA_EXCEEDS_FILE, message, ref.header_offset))

    usable = _usable_bytes(ref, file_size)
    if frame_size and usable % frame_size:
        message = f"{_plural(usable, 'byte')} of data is not a whole number of {frame_size}-byte frames"
        findings.append(Finding(Code.DATA_NOT_FRAME_ALIGNED, message, ref.header_offset))
    return findings


def _usable_bytes(ref: _ChunkRef, file_size: int) -> int:
    available = file_size - ref.payload_offset
    if ref.size == SIZE_UNKNOWN:
        return available
    return min(ref.size, available)


def _fmt_failure(code: Code, message: str, offset: int) -> _FmtResult:
    return _FmtResult((Finding(code, message, offset),), None)


def _zero_size_data_has_bytes(read_at: ReadAt, ref: _ChunkRef, file_size: int) -> bool:
    remaining = file_size - ref.payload_offset
    if remaining == 0:
        return False
    starts_next_chunk = remaining >= CHUNK_HEADER_SIZE and _plausible_id(read_at(ref.payload_offset, 4))
    return not starts_next_chunk


def _resolve_pad(window: bytes, q: int, file_size: int) -> tuple[bool, int]:
    if q == file_size:
        return True, q
    if _plausible_id(window[0:4]) and not _plausible_id(window[1:5]):
        return True, q
    return False, q + 1


def _plausible_id(chunk_id: bytes) -> bool:
    return len(chunk_id) == 4 and all(0x20 <= b <= 0x7E for b in chunk_id)


def _next_offset(ref: _ChunkRef, read_at: ReadAt, file_size: int) -> tuple[int, bool]:
    q = ref.payload_offset + ref.size
    if ref.size % 2 == 0:
        return q, False
    pad_missing, next_offset = _resolve_pad(read_at(q, PAD_WINDOW_SIZE), q, file_size)
    return next_offset, pad_missing


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _file_size(f: BinaryIO) -> int:
    return f.seek(0, os.SEEK_END)


def _read_at(f: BinaryIO, offset: int, length: int) -> bytes:
    f.seek(offset)
    parts = []
    remaining = length
    while remaining > 0:
        part = f.read(remaining)
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)
