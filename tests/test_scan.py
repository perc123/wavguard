import io

import pytest

from builders import chunk, fmt_payload, frames, wav
from wavguard import validate
from wavguard.core import _read_at, _scan_chunks

FMT = chunk(b"fmt ", fmt_payload())  # 24 bytes
DATA = chunk(b"data", frames())  # 68 bytes
LIST = chunk(b"LIST", b"INFO")  # 12 bytes
JUNK = chunk(b"JUNK", b"")  # 8 bytes
ODD = chunk(b"JUNK", b"xyz")  # 3-byte payload + pad = 12 bytes
ODD_NO_PAD = chunk(b"JUNK", b"xyz", pad=False)

FMT_AT_12 = (12, 20, 16)
DATA_AT_36 = (36, 44, 60)
FIRST_AFTER_1024 = 12 + 1024 * len(JUNK)
CHUNK_1025_OFFSET = 12 + len(FMT) + len(DATA) + 1022 * len(JUNK)


def scan(data: bytes):
    f = io.BytesIO(data)
    return _scan_chunks(lambda offset, length: _read_at(f, offset, length), len(data))


def loc(ref):
    return None if ref is None else (ref.header_offset, ref.payload_offset, ref.size)


# name: (file, fmt (header, payload, size), data (header, payload, size), findings, limit_hit)
CASES = {
    "plain": (wav(), FMT_AT_12, DATA_AT_36, [], False),
    "list_before_fmt": (wav([LIST, FMT, DATA]), (24, 32, 16), (48, 56, 60), [], False),
    "fmt_after_data": (wav([DATA, FMT]), (80, 88, 16), (12, 20, 60), [], False),
    "odd_chunk_with_pad": (wav([ODD, FMT, DATA]), (24, 32, 16), (48, 56, 60), [], False),
    "two_fmt_first_wins": (
        wav([FMT, chunk(b"fmt ", fmt_payload(channels=1)), DATA]),
        FMT_AT_12,
        (60, 68, 60),
        [],
        False,
    ),
    "zero_bytes_after_last": (wav([FMT, DATA, b"\x00" * 16]), FMT_AT_12, DATA_AT_36, [], False),
    "list_far_past_eof": (wav([FMT, chunk(b"LIST", b"INFO", size=10_000), DATA]), FMT_AT_12, None, [], False),
    "fmt_truncated": (wav()[:26], FMT_AT_12, None, [], False),
    "data_past_eof": (wav([chunk(b"data", frames(), size=1000), FMT]), None, (12, 20, 1000), [], False),
    "data_size_unknown": (wav([chunk(b"data", frames(), size=0xFFFFFFFF), FMT]), None, (12, 20, 0xFFFFFFFF), [], False),
    "exactly_1024_chunks": (wav([FMT, DATA] + [JUNK] * 1022), FMT_AT_12, DATA_AT_36, [], False),
    "1025_chunks": (
        wav([FMT, DATA] + [JUNK] * 1023),
        FMT_AT_12,
        DATA_AT_36,
        [("LIMIT_EXCEEDED", CHUNK_1025_OFFSET)],
        True,
    ),
    "1030_junk_then_fmt_data": (
        wav([JUNK] * 1030 + [FMT, DATA]),
        None,
        None,
        [("LIMIT_EXCEEDED", FIRST_AFTER_1024)],
        True,
    ),
    # §4.3 rules, which decide where the next header is.
    "odd_chunk_pad_missing": (
        wav([ODD_NO_PAD, FMT, DATA]),
        (23, 31, 16),
        (47, 55, 60),
        [("ODD_CHUNK_PAD_MISSING", 23)],
        False,
    ),
    "odd_chunk_at_eof_no_pad": (
        wav([FMT, DATA, ODD_NO_PAD]),
        FMT_AT_12,
        DATA_AT_36,
        [("ODD_CHUNK_PAD_MISSING", 115)],
        False,
    ),
    "zero_data_then_chunk": (wav([chunk(b"data", b""), FMT]), (20, 28, 16), (12, 20, 0), [], False),
    "zero_data_then_audio": (
        wav([FMT, chunk(b"data", frames(), size=0)]),
        FMT_AT_12,
        (36, 44, 0),
        [("DATA_SIZE_ZERO_WITH_DATA", 44)],
        False,
    ),
    "zero_data_then_3_bytes": (
        wav([FMT, chunk(b"data", b"xyz", size=0, pad=False)]),
        FMT_AT_12,
        (36, 44, 0),
        [("DATA_SIZE_ZERO_WITH_DATA", 44)],
        False,
    ),
}


@pytest.mark.parametrize("name", CASES)
def test_scan(name):
    data, fmt, data_chunk, findings, limit_hit = CASES[name]
    result = scan(data)
    assert loc(result.fmt) == fmt
    assert loc(result.data) == data_chunk
    assert [(f.code, f.offset) for f in result.findings] == findings
    assert result.limit_hit is limit_hit


@pytest.mark.parametrize("n", range(1, 8))
def test_stray_bytes_after_last_chunk_stop_silently(n):
    # Printable bytes, so the stop comes from the 8-byte rule, not from plausibility.
    result = scan(wav([FMT, DATA, b"x" * n]))
    assert loc(result.fmt) == FMT_AT_12
    assert loc(result.data) == DATA_AT_36
    assert list(result.findings) == []
    assert result.limit_hit is False


def test_chunk_ids_are_recorded():
    result = scan(wav())
    assert result.fmt.id == b"fmt "
    assert result.data.id == b"data"


def test_limit_hit_suppresses_missing_chunk_errors():
    report = validate(io.BytesIO(wav([JUNK] * 1030 + [FMT, DATA])))
    assert {f.code for f in report.errors} == {"LIMIT_EXCEEDED"}
    assert report.warnings == []
