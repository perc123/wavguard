import io

from builders import wav
from wavguard import validate
from wavguard.core import _read_at


class OneByteAtATime(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 1))


def test_read_at_collects_short_reads():
    assert _read_at(OneByteAtATime(b"abcdef"), 1, 4) == b"bcde"


def test_read_at_stops_at_end_of_file():
    assert _read_at(OneByteAtATime(b"abc"), 1, 10) == b"bc"


def test_read_at_past_end_returns_empty():
    assert _read_at(OneByteAtATime(b"abc"), 5, 4) == b""


def test_validate_reads_header_through_short_reads():
    report = validate(OneByteAtATime(wav()[:-4]))
    assert [(f.code, f.offset) for f in report.warnings] == [("RIFF_SIZE_EXCEEDS_FILE", 4)]
