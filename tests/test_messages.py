import io

import pytest

from builders import chunk, fmt_payload, frames, wav
from wavguard import validate
from wavguard.core import _plural

A = wav()
DATA_PAYLOAD_START = len(wav([chunk(b"fmt ", fmt_payload())])) + 8


@pytest.mark.parametrize(("n", "expected"), [(0, "0 bytes"), (1, "1 byte"), (2, "2 bytes")])
def test_plural(n, expected):
    assert _plural(n, "byte") == expected


def messages(data: bytes) -> dict[str, str]:
    return {f.code: f.message for f in validate(io.BytesIO(data)).findings}


# name: (file, code, text its message must contain)
SINGULAR = {
    "one_trailing_byte": (A + b"\x80", "TRAILING_BYTES", "1 byte after the RIFF chunk"),
    "one_byte_file": (b"R", "NOT_RIFF_WAVE", "file is 1 byte,"),
    "one_data_byte_present": (A[: DATA_PAYLOAD_START + 1], "DATA_EXCEEDS_FILE", "exceeds the 1 byte present"),
    "one_data_byte_misaligned": (A[: DATA_PAYLOAD_START + 1], "DATA_NOT_FRAME_ALIGNED", "1 byte of data"),
    "mono_8_bit_block_align": (
        wav([chunk(b"fmt ", fmt_payload(channels=1, bits=8, block_align=2)), chunk(b"data", frames(10, 1))]),
        "BLOCK_ALIGN_MISMATCH",
        "1 channel x 1 byte is 1",
    ),
}


@pytest.mark.parametrize("name", SINGULAR)
def test_singular_counts(name):
    data, code, text = SINGULAR[name]
    assert text in messages(data)[code]


def test_pad_message_shows_decoded_chunk_id():
    data = wav(
        [
            chunk(b"fmt ", fmt_payload(channels=1)),
            chunk(b"data", frames(11, 3), pad=False),
            chunk(b"LIST", b"INFO"),
        ]
    )
    message = messages(data)["ODD_CHUNK_PAD_MISSING"]
    assert message.startswith("chunk 'data' has odd size 33")
    assert "b'" not in message
