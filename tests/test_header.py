import struct

import pytest

from wavguard.core import _check_header


def header(riff_size: int, *, magic: bytes = b"RIFF", form: bytes = b"WAVE") -> bytes:
    return magic + struct.pack("<I", riff_size) + form


GOOD = header(4)

# (head, file_size, expected [(code, offset)], keep_going)
CASES = {
    "size_0": (GOOD[:0], 0, [("NOT_RIFF_WAVE", 0)], False),
    "size_5": (GOOD[:5], 5, [("NOT_RIFF_WAVE", 0)], False),
    "size_11": (GOOD[:11], 11, [("NOT_RIFF_WAVE", 0)], False),
    "size_12_minimal": (GOOD, 12, [], True),
    "rf64": (header(4, magic=b"RF64"), 12, [("UNSUPPORTED_FORMAT", 0)], False),
    "bw64": (header(4, magic=b"BW64"), 12, [("UNSUPPORTED_FORMAT", 0)], False),
    "rifx": (header(4, magic=b"RIFX"), 12, [("UNSUPPORTED_FORMAT", 0)], False),
    "wave64_riff_lowercase": (header(4, magic=b"riff"), 12, [("UNSUPPORTED_FORMAT", 0)], False),
    "rf64_larger_file": (header(92, magic=b"RF64"), 100, [("UNSUPPORTED_FORMAT", 0)], False),
    "rf64_8_bytes": (header(4, magic=b"RF64")[:8], 8, [("NOT_RIFF_WAVE", 0)], False),
    "form_avi": (header(4, form=b"AVI "), 12, [("NOT_RIFF_WAVE", 0)], False),
    "form_wave_lowercase": (header(4, form=b"wave"), 12, [("NOT_RIFF_WAVE", 0)], False),
    "riff_unknown_small_file": (header(0xFFFFFFFF), 12, [], True),
    "riff_unknown_large_file": (header(0xFFFFFFFF), 1000, [], True),
    "riff_unknown_huge_file": (header(0xFFFFFFFF), 2**40, [], True),
    "riff_matches": (header(100), 108, [], True),
    "riff_one_over": (header(100), 107, [("RIFF_SIZE_EXCEEDS_FILE", 4)], True),
    "riff_one_under": (header(100), 109, [("TRAILING_BYTES", 108)], True),
    # 0xFFFFFFFE is the largest regular size: it must not be treated as unknown.
    "riff_max_regular_matches": (header(0xFFFFFFFE), 8 + 0xFFFFFFFE, [], True),
    "riff_max_regular_one_over": (header(0xFFFFFFFE), 7 + 0xFFFFFFFE, [("RIFF_SIZE_EXCEEDS_FILE", 4)], True),
}


@pytest.mark.parametrize("name", CASES)
def test_check_header(name):
    head, file_size, expected, keep_going = CASES[name]
    findings, cont = _check_header(head, file_size)
    assert [(f.code, f.offset) for f in findings] == expected
    assert cont is keep_going
