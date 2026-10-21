import pytest

from wavguard.core import _resolve_pad

Q = 100

# name: (window = bytes read at q, at most 5; file size; expected (pad missing, next header offset))
CASES = {
    "eof_at_q": (b"", Q, (True, Q)),
    "pad_then_header": (b"\x00LIST", Q + 20, (False, Q + 1)),
    "header_without_pad": (b"LIST\x04", Q + 20, (True, Q)),
    "both_plausible_assumes_pad": (b"LISTI", Q + 20, (False, Q + 1)),
    "pad_then_3_bytes": (b"\x00ABC", Q + 4, (False, Q + 1)),
    "3_bytes_only": (b"ABC", Q + 3, (False, Q + 1)),
    "1_byte_pad": (b"\x00", Q + 1, (False, Q + 1)),
    # From SPEC §4.3: plausible at q, and only 3 bytes at q + 1, so not plausible there.
    "plausible_at_q_then_eof": (b"LIST", Q + 4, (True, Q)),
}


@pytest.mark.parametrize("name", CASES)
def test_resolve_pad(name):
    window, file_size, expected = CASES[name]
    assert _resolve_pad(window, Q, file_size) == expected
