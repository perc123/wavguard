import io
import wave

from builders import BITS, CHANNELS, N_FRAMES, wav


def test_case_a_reads_with_stdlib_wave():
    with wave.open(io.BytesIO(wav())) as w:
        assert w.getnchannels() == CHANNELS
        assert w.getsampwidth() == BITS // 8
        assert w.getnframes() == N_FRAMES
