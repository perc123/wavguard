import os
import struct
from typing import BinaryIO

from wavguard.model import Code, Finding, Report

HEADER_SIZE = 12
UNSUPPORTED_MAGICS = frozenset({b"RF64", b"BW64", b"RIFX", b"riff"})
SIZE_UNKNOWN = 0xFFFFFFFF


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
