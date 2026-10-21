import pytest

from wavguard.core import _check_data, _ChunkRef

HEADER = 36
PAYLOAD = 44
UNKNOWN = 0xFFFFFFFF


def data_ref(size: int) -> _ChunkRef:
    return _ChunkRef(b"data", HEADER, PAYLOAD, size)


# name: (declared size, bytes available after the payload start, frame size, expected [(code, offset)])
CASES = {
    "exact": (600, 600, 6, []),
    "cut_4": (600, 596, 6, [("DATA_EXCEEDS_FILE", HEADER), ("DATA_NOT_FRAME_ALIGNED", HEADER)]),
    "cut_one_frame": (600, 594, 6, [("DATA_EXCEEDS_FILE", HEADER)]),
    "patched_after_cut": (596, 596, 6, [("DATA_NOT_FRAME_ALIGNED", HEADER)]),
    "unknown_size_aligned": (UNKNOWN, 600, 6, [("DATA_SIZE_UNKNOWN", HEADER)]),
    # §4.2 step 4: for an unknown size, usable bytes run to EOF and are still checked for alignment.
    "unknown_size_misaligned": (UNKNOWN, 596, 6, [("DATA_SIZE_UNKNOWN", HEADER), ("DATA_NOT_FRAME_ALIGNED", HEADER)]),
    "extra_bytes_after_chunk": (600, 1000, 6, []),
    "zero_size_is_scanner_rule": (0, 600, 6, []),
    "no_frame_size_cut_4": (600, 596, None, [("DATA_EXCEEDS_FILE", HEADER)]),
    "no_frame_size_unknown": (UNKNOWN, 596, None, [("DATA_SIZE_UNKNOWN", HEADER)]),
}


@pytest.mark.parametrize("name", CASES)
def test_check_data(name):
    declared, available, frame_size, expected = CASES[name]
    findings = _check_data(data_ref(declared), PAYLOAD + available, frame_size)
    assert sorted((f.code, f.offset) for f in findings) == sorted(expected)
