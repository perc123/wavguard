import io
import os
import random
import re
import struct
import time
import zlib

import pytest

from builders import chunk, fmt_payload, frames, wav
from wavguard import Code, validate
from wavguard.core import MAX_CHUNKS
from wavguard.model import Severity

ITERATIONS = int(os.environ.get("WAVGUARD_FUZZ_ITERS", "5000"))

# Per chunk the scanner reads one 8-byte header and at most one 5-byte pad window.
MAX_READ_CALLS = 2 * MAX_CHUNKS + 16
MAX_BYTES_READ = 32 * 1024
WRONG_PLURAL = re.compile(r"(?<![\d.])1 (bytes|channels)\b")

FMT = chunk(b"fmt ", fmt_payload())
DATA = chunk(b"data", frames())
LIST = chunk(b"LIST", b"INFO")
FMT_MONO = chunk(b"fmt ", fmt_payload(channels=1))

BASES = {
    "A": wav(),
    "F_extensible": wav([chunk(b"fmt ", fmt_payload(extensible=True)), DATA]),
    "J_trailing_list": wav([FMT, DATA, LIST]),
    "T_odd_padded": wav([FMT_MONO, chunk(b"data", frames(11, 3)), LIST]),
    "U_odd_no_pad": wav([FMT_MONO, chunk(b"data", frames(11, 3), pad=False), LIST]),
    "W_fmt_only": wav([FMT]),
    "junk_chunks": wav([chunk(b"JUNK", b"ab"), FMT, chunk(b"JUNK", b"xyz"), DATA, chunk(b"JUNK", b"")]),
}


class CountingBytesIO(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.calls = 0
        self.bytes_read = 0

    def read(self, size=-1):
        assert size is not None and size >= 0, "read() called without a size"
        out = super().read(size)
        self.calls += 1
        self.bytes_read += len(out)
        return out


def check(data: bytes, context: str) -> None:
    def where() -> str:
        return f"{context}; data={data.hex()}" if len(data) <= 512 else context

    stream = CountingBytesIO(data)
    try:
        report = validate(stream)
        again = validate(io.BytesIO(data))
        strict = validate(io.BytesIO(data), strict=True)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"validate raised {exc!r}: {where()}")

    for f in report.findings:
        assert isinstance(f.code, Code), where()
        assert not WRONG_PLURAL.search(f.message), f"{f.message!r}: {where()}"
        assert f.offset is None or 0 <= f.offset <= len(data), f"{f}: {where()}"

    assert all(f.severity is Severity.ERROR for f in report.errors), where()
    assert all(f.severity is Severity.WARNING for f in report.warnings), where()
    assert len(report.errors) + len(report.warnings) == len(report.findings), where()
    assert report.ok == (not report.errors), where()
    assert strict.ok == (not report.errors and not report.warnings), where()

    fields = (report.channels, report.bits_per_sample, report.sample_rate, report.frame_size)
    if None in fields:
        assert fields == (None,) * 4, where()
        assert (report.frames_declared, report.complete_frames) == (None, None), where()
    else:
        assert all(v > 0 for v in fields), where()
        assert report.frame_size == report.channels * report.bits_per_sample // 8, where()

    assert report == again, where()

    assert stream.calls <= MAX_READ_CALLS, f"{stream.calls} read calls: {where()}"
    assert stream.bytes_read <= MAX_BYTES_READ, f"{stream.bytes_read} bytes read: {where()}"


# Layer 1: every truncation of every base file.

TRUNCATIONS = [(name, n) for name, data in BASES.items() for n in range(len(data) + 1)]


@pytest.mark.parametrize(("name", "length"), TRUNCATIONS)
def test_truncation(name, length):
    check(BASES[name][:length], f"base={name} truncated to {length}")


# Layer 2: seeded random mutation.

SPECIAL_VALUES = (0, 1, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000)


def mutate(rng: random.Random, data: bytes) -> tuple[str, bytes]:
    buf = bytearray(data)
    kind = rng.choice(["bytes", "field", "delete", "insert", "truncate"])
    if not buf and kind != "insert":
        kind = "insert"

    if kind == "truncate":
        inner, mutated = mutate(rng, data)
        cut = rng.randint(0, len(mutated))
        return f"{inner} then truncate at {cut}", mutated[:cut]
    if kind == "bytes":
        for _ in range(rng.randint(1, 4)):
            pos = rng.randrange(len(buf))
            buf[pos] = buf[pos] ^ (1 << rng.randrange(8)) if rng.random() < 0.5 else rng.randrange(256)
        return "bytes", bytes(buf)
    if kind == "field" and len(buf) >= 4:
        pos = rng.randrange(len(buf) - 3)
        (current,) = struct.unpack_from("<I", buf, pos)
        value = rng.choice((*SPECIAL_VALUES, (current + 1) & 0xFFFFFFFF, (current - 1) & 0xFFFFFFFF))
        struct.pack_into("<I", buf, pos, value)
        return f"field at {pos} = {value:#x}", bytes(buf)
    if kind == "delete":
        start = rng.randrange(len(buf))
        end = rng.randint(start + 1, len(buf))
        del buf[start:end]
        return f"delete {start}:{end}", bytes(buf)
    start = rng.randint(0, len(buf))
    piece = bytes(rng.randrange(256) for _ in range(rng.randint(1, 32)))
    buf[start:start] = piece
    return f"insert {len(piece)} bytes at {start}", bytes(buf)


@pytest.mark.parametrize("name", BASES)
def test_random_mutation(name):
    seed = zlib.crc32(name.encode())
    rng = random.Random(seed)
    for i in range(ITERATIONS):
        label, data = mutate(rng, BASES[name])
        check(data, f"base={name} seed={seed} iteration={i} mutation={label}")


# Structured mutations aimed at the risky paths.

SIZE_VALUES = (0, 15, 16, 39, 40, 1024, 1025, 0xFFFFFFFF)


def chunk_headers(data: bytes) -> dict[bytes, int]:
    offsets: dict[bytes, int] = {}
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        offsets.setdefault(cid, pos)
        pos += 8 + size + (size & 1)
    return offsets


def with_field(data: bytes, pos: int, value: int) -> bytes:
    buf = bytearray(data)
    struct.pack_into("<I", buf, pos, value)
    return bytes(buf)


def structured_mutations():
    for name, data in BASES.items():
        headers = chunk_headers(data)
        if b"fmt " in headers:
            pos = headers[b"fmt "] + 4
            for value in SIZE_VALUES:
                yield f"{name}_fmt_size_{value:#x}", with_field(data, pos, value)
        if b"data" in headers:
            pos = headers[b"data"] + 4
            (actual,) = struct.unpack_from("<I", data, pos)
            for value in (*SIZE_VALUES, actual - 1, actual + 1):
                yield f"{name}_data_size_{value:#x}", with_field(data, pos, value)
        for garbage in (b"\x00\x01\x02\x03", b"\xff\xfe\x80\x7f"):
            yield f"{name}_first_id_{garbage.hex()}", data[:12] + garbage + data[16:]


STRUCTURED = dict(structured_mutations())


@pytest.mark.parametrize("label", STRUCTURED)
def test_structured_mutation(label):
    check(STRUCTURED[label], f"structured={label}")


# Bounded work at the extremes.


def test_worst_case_chunk_count_stays_under_ceiling():
    odd_chunks = [chunk(b"JUNK", b"x")] * (MAX_CHUNKS + 100)
    data = wav([FMT, DATA, *odd_chunks])
    check(data, "1124 odd chunks")
    assert {f.code for f in validate(io.BytesIO(data)).errors} == {"LIMIT_EXCEEDED"}


def test_large_audio_is_not_read():
    data = wav([FMT, chunk(b"data", frames(200_000))])
    stream = CountingBytesIO(data)
    assert validate(stream).ok
    assert stream.bytes_read < 1024


def test_huge_declared_sizes_return_quickly():
    data = wav([FMT, chunk(b"data", frames(1), size=0xFFFFFFFE)], riff_size=0xFFFFFFFE)
    stream = CountingBytesIO(data)
    start = time.perf_counter()
    report = validate(stream)
    assert time.perf_counter() - start < 1.0
    assert stream.bytes_read < 1024
    assert {f.code for f in report.errors} == {"DATA_EXCEEDS_FILE"}
    assert {f.code for f in report.warnings} == {"RIFF_SIZE_EXCEEDS_FILE"}
