import io

import pytest

from builders import FLOAT, chunk, fmt_payload, frames, wav
from wavguard import validate
from wavguard.core import _parse_fmt

HEADER = 12
PAYLOAD = 20


def parse(payload: bytes):
    return _parse_fmt(payload, HEADER, PAYLOAD)


def located(findings):
    return [(f.code, f.offset) for f in findings]


def info_tuple(info):
    return (info.tag, info.channels, info.sample_rate, info.bits_per_sample, info.block_align, info.frame_size)


def bad_guid_suffix() -> bytes:
    payload = bytearray(fmt_payload(extensible=True))
    payload[-1] ^= 0xFF
    return bytes(payload)


# name: (payload, findings, info as (tag, channels, rate, bits, block_align, frame_size))
VALID = {
    "pcm_24_stereo": (fmt_payload(), [], (1, 2, 48000, 24, 6, 6)),
    "float_32_mono": (fmt_payload(FLOAT, channels=1, bits=32), [], (3, 1, 48000, 32, 4, 4)),
    "float_64_stereo": (fmt_payload(FLOAT, bits=64), [], (3, 2, 48000, 64, 16, 16)),
    "extensible_pcm_40_bytes": (fmt_payload(extensible=True), [], (1, 2, 48000, 24, 6, 6)),
    "extensible_float": (fmt_payload(FLOAT, bits=32, extensible=True), [], (3, 2, 48000, 32, 8, 8)),
    "block_align_mismatch": (
        fmt_payload(block_align=8),
        [("BLOCK_ALIGN_MISMATCH", PAYLOAD + 12)],
        (1, 2, 48000, 24, 8, 6),
    ),
}

# name: (payload, findings); info must be None
INVALID = {
    "extensible_bad_guid_suffix": (bad_guid_suffix(), [("UNSUPPORTED_FORMAT", PAYLOAD + 24)]),
    "extensible_adpcm": (fmt_payload(tag=2, extensible=True), [("UNSUPPORTED_FORMAT", PAYLOAD + 24)]),
    "extensible_39_bytes": (fmt_payload(extensible=True)[:39], [("FMT_INVALID", HEADER)]),
    "pcm_15_bytes": (fmt_payload()[:15], [("FMT_INVALID", HEADER)]),
    "tag_2": (fmt_payload(tag=2), [("UNSUPPORTED_FORMAT", PAYLOAD)]),
    "tag_2_15_bytes": (fmt_payload(tag=2)[:15], [("FMT_INVALID", HEADER)]),
    "channels_0": (fmt_payload(channels=0), [("FMT_INVALID", HEADER)]),
    "rate_0": (fmt_payload(rate=0), [("FMT_INVALID", HEADER)]),
    "block_align_0": (fmt_payload(block_align=0), [("FMT_INVALID", HEADER)]),
    "bits_0": (fmt_payload(bits=0), [("FMT_INVALID", HEADER)]),
    "bits_12": (fmt_payload(bits=12), [("FMT_INVALID", HEADER)]),
    "channels_0_and_rate_0": (fmt_payload(channels=0, rate=0), [("FMT_INVALID", HEADER)]),
    "float_16_bits": (fmt_payload(FLOAT, bits=16), [("FMT_INVALID", HEADER)]),
}


@pytest.mark.parametrize("name", VALID)
def test_valid_fmt(name):
    payload, findings, info = VALID[name]
    result = parse(payload)
    assert located(result.findings) == findings
    assert info_tuple(result.info) == info


@pytest.mark.parametrize("name", INVALID)
def test_invalid_fmt(name):
    payload, findings = INVALID[name]
    result = parse(payload)
    assert located(result.findings) == findings
    assert result.info is None


def test_payload_lengths_are_what_the_rows_claim():
    assert len(fmt_payload()) == 16
    assert len(fmt_payload(extensible=True)) == 40


def errors_of(data: bytes):
    return located(validate(io.BytesIO(data)).errors)


def fmt_of_size(size: int) -> bytes:
    return chunk(b"fmt ", fmt_payload() + b"\x00" * (size - 16))


def test_truncated_fmt_is_invalid():
    assert ("FMT_INVALID", HEADER) in errors_of(wav()[:26])


def test_fmt_at_size_limit_is_parsed():
    report = validate(io.BytesIO(wav([fmt_of_size(1024), chunk(b"data", frames())])))
    assert report.errors == []
    assert report.channels == 2


def test_fmt_over_size_limit_is_not_parsed():
    report = validate(io.BytesIO(wav([fmt_of_size(1025), chunk(b"data", frames())])))
    assert located(report.errors) == [("LIMIT_EXCEEDED", HEADER)]
    assert report.channels is None


def test_valid_fmt_fills_report():
    report = validate(io.BytesIO(wav()))
    assert (report.channels, report.bits_per_sample, report.sample_rate, report.frame_size) == (2, 24, 48000, 6)


@pytest.mark.parametrize(
    "fmt",
    [fmt_payload(channels=0), fmt_payload(tag=2), fmt_payload()[:15]],
    ids=["invalid", "unsupported", "too_short"],
)
def test_invalid_or_unsupported_fmt_leaves_report_fields_none(fmt):
    report = validate(io.BytesIO(wav([chunk(b"fmt ", fmt), chunk(b"data", frames())])))
    assert (report.channels, report.bits_per_sample, report.sample_rate, report.frame_size) == (None,) * 4
