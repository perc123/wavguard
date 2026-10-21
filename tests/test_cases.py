import io
from dataclasses import dataclass

import pytest

from builders import FRAME_SIZE, chunk, fmt_payload, frames, wav
from wavguard import validate

FMT = chunk(b"fmt ", fmt_payload())
FMT_EXT = chunk(b"fmt ", fmt_payload(extensible=True))
DATA = chunk(b"data", frames())
LIST = chunk(b"LIST", b"INFO")  # size 4 < 0x20, as SPEC §7 requires for U
A = wav()

# 24-bit mono (3-byte frames) with 11 frames: odd-length data that is still frame-aligned.
FMT_MONO = chunk(b"fmt ", fmt_payload(channels=1))
ODD_AUDIO = frames(11, 3)

CASES = {
    "A_intact": (A, set(), set()),
    "B_cut_4": (A[:-4], {"DATA_EXCEEDS_FILE", "DATA_NOT_FRAME_ALIGNED"}, {"RIFF_SIZE_EXCEEDS_FILE"}),
    "C_cut_frame": (A[:-FRAME_SIZE], {"DATA_EXCEEDS_FILE"}, {"RIFF_SIZE_EXCEEDS_FILE"}),
    "D_cut_4_patched": (wav([FMT, chunk(b"data", frames()[:-4])]), {"DATA_NOT_FRAME_ALIGNED"}, set()),
    "E_ext_cut_4": (
        wav([FMT_EXT, DATA])[:-4],
        {"DATA_EXCEEDS_FILE", "DATA_NOT_FRAME_ALIGNED"},
        {"RIFF_SIZE_EXCEEDS_FILE"},
    ),
    "F_ext_intact": (wav([FMT_EXT, DATA]), set(), set()),
    "G_data_size_unknown": (wav([FMT, chunk(b"data", frames(), size=0xFFFFFFFF)]), set(), {"DATA_SIZE_UNKNOWN"}),
    "H_data_size_0_riff_8": (
        wav([FMT, chunk(b"data", frames(), size=0)], riff_size=8),
        {"DATA_SIZE_ZERO_WITH_DATA"},
        {"TRAILING_BYTES"},
    ),
    "I_data_size_0": (wav([FMT, chunk(b"data", frames(), size=0)]), {"DATA_SIZE_ZERO_WITH_DATA"}, set()),
    "J_trailing_list": (wav([FMT, DATA, LIST]), set(), set()),
    "K_block_align_8": (wav([chunk(b"fmt ", fmt_payload(block_align=8)), DATA]), {"BLOCK_ALIGN_MISMATCH"}, set()),
    "L_empty": (b"", {"NOT_RIFF_WAVE"}, set()),
    "L_5_bytes": (A[:5], {"NOT_RIFF_WAVE"}, set()),
    "M_rf64_magic": (wav(magic=b"RF64"), {"UNSUPPORTED_FORMAT"}, set()),
    "N_no_fmt": (wav([DATA]), {"NO_FMT_CHUNK"}, set()),
    "O_no_data": (wav([FMT]), {"NO_DATA_CHUNK"}, set()),
    "P_adpcm": (wav([chunk(b"fmt ", fmt_payload(tag=2)), DATA]), {"UNSUPPORTED_FORMAT"}, set()),
    "Q_zero_channels": (wav([chunk(b"fmt ", fmt_payload(channels=0)), DATA]), {"FMT_INVALID"}, set()),
    "R_riff_size_too_big": (wav(riff_size=len(A) - 8 + 100), set(), {"RIFF_SIZE_EXCEEDS_FILE"}),
    "S_trailing_bytes": (A + frames(1), set(), {"TRAILING_BYTES"}),
    "T_odd_data_padded": (wav([FMT_MONO, chunk(b"data", ODD_AUDIO), LIST]), set(), set()),
    "U_odd_data_no_pad": (
        wav([FMT_MONO, chunk(b"data", ODD_AUDIO, pad=False), LIST]),
        set(),
        {"ODD_CHUNK_PAD_MISSING"},
    ),
    "V_5000_chunks": (wav([FMT, DATA] + [chunk(b"JUNK", b"")] * 5000), {"LIMIT_EXCEEDED"}, set()),
    # fmt payload starts at offset 20; cut 10 bytes into it.
    "W_cut_in_fmt": (A[:30], {"FMT_INVALID", "NO_DATA_CHUNK"}, {"RIFF_SIZE_EXCEEDS_FILE"}),
}


@dataclass(frozen=True)
class Layout:
    riff_header_end: int
    fmt_payload_start: int
    fmt_end: int
    data_payload_start: int
    end: int


def layout_of(fmt_chunk: bytes, data_chunk: bytes) -> Layout:
    chunk_header = len(chunk(b"fmt ", b""))
    riff_header_end = len(wav([]))
    fmt_end = riff_header_end + len(fmt_chunk)
    return Layout(
        riff_header_end=riff_header_end,
        fmt_payload_start=riff_header_end + chunk_header,
        fmt_end=fmt_end,
        data_payload_start=fmt_end + chunk_header,
        end=fmt_end + len(data_chunk),
    )


def expected_for_cut(n: int, at: Layout) -> tuple[set[str], set[str]]:
    if n < at.riff_header_end:
        return {"NOT_RIFF_WAVE"}, set()
    warnings = {"RIFF_SIZE_EXCEEDS_FILE"} if n < at.end else set()
    if n < at.fmt_payload_start:
        return {"NO_FMT_CHUNK", "NO_DATA_CHUNK"}, warnings
    if n < at.fmt_end:
        return {"FMT_INVALID", "NO_DATA_CHUNK"}, warnings
    if n < at.data_payload_start:
        return {"NO_DATA_CHUNK"}, warnings
    if n < at.end:
        aligned = (n - at.data_payload_start) % FRAME_SIZE == 0
        return {"DATA_EXCEEDS_FILE"} | (set() if aligned else {"DATA_NOT_FRAME_ALIGNED"}), warnings
    return set(), set()


@pytest.mark.parametrize("fmt_chunk", [FMT, FMT_EXT], ids=["A_pcm", "F_extensible"])
def test_every_cut_has_exact_findings(fmt_chunk):
    data = wav([fmt_chunk, DATA])
    at = layout_of(fmt_chunk, DATA)
    assert at.end == len(data)
    mismatches = []
    for n in range(len(data) + 1):
        report = validate(io.BytesIO(data[:n]))
        actual = ({f.code for f in report.errors}, {f.code for f in report.warnings})
        if actual != expected_for_cut(n, at):
            mismatches.append((n, actual, expected_for_cut(n, at)))
    assert mismatches == []


@pytest.mark.parametrize("name", CASES)
def test_case(name):
    data, errors, warnings = CASES[name]
    report = validate(io.BytesIO(data))
    assert {f.code for f in report.errors} == errors
    assert {f.code for f in report.warnings} == warnings
