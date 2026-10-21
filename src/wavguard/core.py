import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from wavguard.model import Code, Finding, Report

HEADER_SIZE = 12
UNSUPPORTED_MAGICS = frozenset({b"RF64", b"BW64", b"RIFX", b"riff"})
SIZE_UNKNOWN = 0xFFFFFFFF
CHUNK_HEADER_SIZE = 8
MAX_CHUNKS = 1024

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

    scan = _scan_chunks(lambda offset, length: _read_at(f, offset, length), file_size)
    findings.extend(scan.findings)
    if not scan.limit_hit:
        if scan.fmt is None:
            findings.append(Finding(Code.NO_FMT_CHUNK, "no fmt chunk found", None))
        if scan.data is None:
            findings.append(Finding(Code.NO_DATA_CHUNK, "no data chunk found", None))
    return Report(findings=tuple(findings), strict=strict)


def _check_header(head: bytes, file_size: int) -> tuple[list[Finding], bool]:
    if file_size < HEADER_SIZE:
        message = f"file is {file_size} bytes, shorter than the {HEADER_SIZE}-byte RIFF header"
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
            message = f"RIFF size {riff_size} needs {riff_end} bytes, but the file is {file_size} bytes"
            findings.append(Finding(Code.RIFF_SIZE_EXCEEDS_FILE, message, 4))
        elif file_size > riff_end:
            message = f"{file_size - riff_end} bytes follow the end of the RIFF chunk at {riff_end}"
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

        if chunk_id == b"data" and size == SIZE_UNKNOWN:
            break
        if ref.payload_offset + size > file_size:
            break
        offset = _next_offset(ref)

    return _ScanResult(fmt, data, tuple(findings), limit_hit)


def _plausible_id(chunk_id: bytes) -> bool:
    return len(chunk_id) == 4 and all(0x20 <= b <= 0x7E for b in chunk_id)


def _next_offset(ref: _ChunkRef) -> int:
    return ref.payload_offset + ref.size + (ref.size & 1)


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
