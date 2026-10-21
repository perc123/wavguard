import dataclasses
import io

import pytest

from builders import chunk, fmt_payload, frames, wav
from wavguard import Code, Finding, Report, validate
from wavguard.model import Severity


@pytest.fixture
def case_a_path(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(wav())
    return path


def test_str_path_is_ok(case_a_path):
    assert validate(str(case_a_path)).ok


def test_pathlib_path_is_ok(case_a_path):
    assert validate(case_a_path).ok


def test_bytesio_is_ok():
    assert validate(io.BytesIO(wav())).ok


class NonSeekable(io.BytesIO):
    def seekable(self):
        return False


def test_non_seekable_raises_value_error():
    with pytest.raises(ValueError):
        validate(NonSeekable(wav()))


def test_text_mode_file_raises_type_error(case_a_path):
    with open(case_a_path, encoding="latin-1") as f, pytest.raises(TypeError):
        validate(f)  # type: ignore[arg-type]


def test_missing_path_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate(tmp_path / "missing.wav")


def test_warning_only_report_is_ok_unless_strict():
    findings = (Finding(Code.TRAILING_BYTES, "trailing bytes", 104),)
    assert Report(findings).ok
    assert not Report(findings, strict=True).ok


def case_u() -> bytes:
    return wav(
        [
            chunk(b"fmt ", fmt_payload(channels=1)),
            chunk(b"data", frames(11, 3), pad=False),
            chunk(b"LIST", b"INFO"),
        ]
    )


def test_missing_pad_is_ok_when_not_strict():
    report = validate(io.BytesIO(case_u()))
    assert [f.code for f in report.warnings] == ["ODD_CHUNK_PAD_MISSING"]
    assert report.ok


def test_missing_pad_fails_strict():
    assert not validate(io.BytesIO(case_u()), strict=True).ok


def test_finding_is_frozen():
    finding = Finding(Code.NO_DATA_CHUNK, "no data chunk", None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.offset = 0  # type: ignore[misc]


def test_severities_match_spec():
    spec_errors = {
        "NOT_RIFF_WAVE",
        "UNSUPPORTED_FORMAT",
        "NO_FMT_CHUNK",
        "NO_DATA_CHUNK",
        "FMT_INVALID",
        "BLOCK_ALIGN_MISMATCH",
        "DATA_EXCEEDS_FILE",
        "DATA_NOT_FRAME_ALIGNED",
        "DATA_SIZE_ZERO_WITH_DATA",
        "LIMIT_EXCEEDED",
    }
    spec_warnings = {
        "DATA_SIZE_UNKNOWN",
        "RIFF_SIZE_EXCEEDS_FILE",
        "TRAILING_BYTES",
        "ODD_CHUNK_PAD_MISSING",
    }
    severity_of = {code: Finding(code, "", None).severity for code in Code}
    assert {c for c, s in severity_of.items() if s is Severity.ERROR} == spec_errors
    assert {c for c, s in severity_of.items() if s is Severity.WARNING} == spec_warnings
