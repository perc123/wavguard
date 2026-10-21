import io
import random
import zlib

import pytest

from builders import wav
from test_fuzz import BASES, ITERATIONS, mutate
from wavguard import validate

sf = pytest.importorskip("soundfile")

pytestmark = pytest.mark.differential

ABSORBED_BY_READERS = {"DATA_NOT_FRAME_ALIGNED", "DATA_EXCEEDS_FILE"}


def soundfile_frames(data: bytes) -> int | None:
    try:
        return sf.info(io.BytesIO(data)).frames
    except Exception:  # noqa: BLE001
        return None


def test_soundfile_silently_absorbs_truncation():
    truncated = wav()[:-4]
    report = validate(io.BytesIO(truncated))
    assert {f.code for f in report.errors} == ABSORBED_BY_READERS
    assert report.frames_declared == 10
    assert soundfile_frames(truncated) == report.complete_frames == 9


@pytest.mark.parametrize("name", BASES)
def test_agrees_with_soundfile_when_valid(name):
    seed = zlib.crc32(name.encode())
    rng = random.Random(seed)
    for i in range(ITERATIONS):
        label, data = mutate(rng, BASES[name])
        report = validate(io.BytesIO(data))
        if report.errors:
            continue
        frames = soundfile_frames(data)
        context = f"base={name} seed={seed} iteration={i} mutation={label} data={data.hex()}"
        assert frames == report.complete_frames, f"soundfile {frames} vs wavguard {report.complete_frames}: {context}"
