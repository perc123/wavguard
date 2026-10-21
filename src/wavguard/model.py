from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Code(StrEnum):
    NOT_RIFF_WAVE = "NOT_RIFF_WAVE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    NO_FMT_CHUNK = "NO_FMT_CHUNK"
    NO_DATA_CHUNK = "NO_DATA_CHUNK"
    FMT_INVALID = "FMT_INVALID"
    BLOCK_ALIGN_MISMATCH = "BLOCK_ALIGN_MISMATCH"
    DATA_EXCEEDS_FILE = "DATA_EXCEEDS_FILE"
    DATA_NOT_FRAME_ALIGNED = "DATA_NOT_FRAME_ALIGNED"
    DATA_SIZE_ZERO_WITH_DATA = "DATA_SIZE_ZERO_WITH_DATA"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    DATA_SIZE_UNKNOWN = "DATA_SIZE_UNKNOWN"
    RIFF_SIZE_EXCEEDS_FILE = "RIFF_SIZE_EXCEEDS_FILE"
    TRAILING_BYTES = "TRAILING_BYTES"
    ODD_CHUNK_PAD_MISSING = "ODD_CHUNK_PAD_MISSING"


SEVERITY_OF: dict[Code, Severity] = {
    Code.NOT_RIFF_WAVE: Severity.ERROR,
    Code.UNSUPPORTED_FORMAT: Severity.ERROR,
    Code.NO_FMT_CHUNK: Severity.ERROR,
    Code.NO_DATA_CHUNK: Severity.ERROR,
    Code.FMT_INVALID: Severity.ERROR,
    Code.BLOCK_ALIGN_MISMATCH: Severity.ERROR,
    Code.DATA_EXCEEDS_FILE: Severity.ERROR,
    Code.DATA_NOT_FRAME_ALIGNED: Severity.ERROR,
    Code.DATA_SIZE_ZERO_WITH_DATA: Severity.ERROR,
    Code.LIMIT_EXCEEDED: Severity.ERROR,
    Code.DATA_SIZE_UNKNOWN: Severity.WARNING,
    Code.RIFF_SIZE_EXCEEDS_FILE: Severity.WARNING,
    Code.TRAILING_BYTES: Severity.WARNING,
    Code.ODD_CHUNK_PAD_MISSING: Severity.WARNING,
}


@dataclass(frozen=True)
class Finding:
    code: Code
    message: str
    offset: int | None

    @property
    def severity(self) -> Severity:
        return SEVERITY_OF[self.code]


def _file_order(findings: tuple[Finding, ...], severity: Severity) -> list[Finding]:
    selected = [f for f in findings if f.severity is severity]
    return sorted(selected, key=lambda f: (f.offset is None, f.offset or 0))


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]
    strict: bool = False
    channels: int | None = None
    bits_per_sample: int | None = None
    sample_rate: int | None = None
    frame_size: int | None = None
    frames_declared: int | None = None
    complete_frames: int | None = None

    @property
    def errors(self) -> list[Finding]:
        return _file_order(self.findings, Severity.ERROR)

    @property
    def warnings(self) -> list[Finding]:
        return _file_order(self.findings, Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors and not (self.strict and self.warnings)
