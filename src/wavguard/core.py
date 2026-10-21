import os
from typing import BinaryIO

from wavguard.model import Report


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
    return Report(findings=(), strict=strict)


def _file_size(f: BinaryIO) -> int:
    return f.seek(0, os.SEEK_END)


def _read_at(f: BinaryIO, offset: int, length: int) -> bytes:
    f.seek(offset)
    return f.read(length)
