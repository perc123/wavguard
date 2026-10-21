import os
import subprocess
import sys
import threading

import pytest

import wavguard
from builders import chunk, fmt_payload, frames, wav
from wavguard import cli


@pytest.fixture
def write(tmp_path):
    def _write(data: bytes, name: str = "f.wav") -> str:
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)

    return _write


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def test_valid_file(capsys, write):
    path = write(wav())
    code, out, err = run(capsys, path)
    assert (code, out, err) == (0, [f"{path}: OK"], "")


def test_errors_then_warnings_then_summary(capsys, write):
    path = write(wav()[:-4])  # case B
    code, out, err = run(capsys, path)
    assert code == 1
    assert err == ""
    assert out[0].startswith(f"{path}:36: error DATA_EXCEEDS_FILE ")
    assert out[1].startswith(f"{path}:36: error DATA_NOT_FRAME_ALIGNED ")
    assert out[2].startswith(f"{path}:4: warning RIFF_SIZE_EXCEEDS_FILE ")
    assert out[3] == f"{path}: FAILED (2 errors, 1 warning)"
    assert len(out) == 4


def test_offset_none_omits_offset(capsys, write):
    path = write(wav([chunk(b"fmt ", fmt_payload())]))  # case O
    code, out, _ = run(capsys, path)
    assert code == 1
    assert out[0].startswith(f"{path}: error NO_DATA_CHUNK ")
    assert out[-1] == f"{path}: FAILED (1 error)"


def test_warning_only_passes(capsys, write):
    path = write(wav() + frames(1))  # case S
    code, out, _ = run(capsys, path)
    assert code == 0
    assert out[0].startswith(f"{path}:104: warning TRAILING_BYTES ")
    assert out[-1] == f"{path}: OK (1 warning)"


def test_warning_only_fails_strict(capsys, write):
    path = write(wav() + frames(1))
    code, out, _ = run(capsys, "--strict", path)
    assert code == 1
    assert out[-1] == f"{path}: FAILED (1 warning)"


def test_readable_non_wav_is_a_finding(capsys, write):
    path = write(b"")  # case L
    code, out, err = run(capsys, path)
    assert code == 1
    assert err == ""
    assert out[0].startswith(f"{path}:0: error NOT_RIFF_WAVE ")
    assert out[1:] == [f"{path}: FAILED (1 error)"]


def assert_cannot_read(capsys, path):
    code, out, err = run(capsys, path)
    assert code == 2
    assert out == []
    assert path in err


def test_missing_file(capsys, tmp_path):
    assert_cannot_read(capsys, str(tmp_path / "missing.wav"))


def test_directory(capsys, tmp_path):
    assert_cannot_read(capsys, str(tmp_path))


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions as non-root")
def test_permission_denied(capsys, write):
    path = write(wav())
    os.chmod(path, 0)
    try:
        assert_cannot_read(capsys, path)
    finally:
        os.chmod(path, 0o644)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_unseekable_input(capsys, tmp_path):
    fifo = str(tmp_path / "pipe.wav")
    os.mkfifo(fifo)

    def open_writer_end():
        open(fifo, "wb").close()  # unblocks the reader's open()

    writer = threading.Thread(target=open_writer_end, daemon=True)
    writer.start()
    assert_cannot_read(capsys, fifo)
    writer.join(timeout=5)


def test_no_arguments_is_usage_error(capsys):
    code, out, err = run(capsys)
    assert code == 2
    assert out == []
    assert "usage" in err


def test_two_files_is_usage_error(capsys, write):
    code, out, err = run(capsys, write(wav(), "a.wav"), write(wav(), "b.wav"))
    assert code == 2
    assert out == []
    assert "usage" in err


def test_version(capsys):
    code, out, err = run(capsys, "--version")
    assert (code, out, err) == (0, [f"wavguard {wavguard.__version__}"], "")


def test_internal_error_exits_2(capsys, write, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "validate", boom)
    code, out, err = run(capsys, write(wav()))
    assert code == 2
    assert out == []
    assert err.startswith("wavguard: internal error: ")
    assert "boom" in err


def test_python_dash_m(write):
    path = write(wav()[:-4])
    result = subprocess.run([sys.executable, "-m", "wavguard", path], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == f"{path}: FAILED (2 errors, 1 warning)"
