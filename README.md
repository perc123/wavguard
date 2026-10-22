# wavguard

[![CI](https://github.com/perc123/wavguard/actions/workflows/ci.yml/badge.svg)](https://github.com/perc123/wavguard/actions/workflows/ci.yml)

A strict, dependency-free WAV validator that reports truncated or inconsistent files instead of silently reading around them.

## The problem

Take a 3-channel, 24-bit WAV file with 4,800 frames and cut 4 bytes off the end. The last frame is now incomplete. python-soundfile (libsndfile 1.2.2) reports and reads 4,799 frames. Python's `wave` module reports 4,800 frames but returns 4,799. Neither one warns. Both behaviours are reasonable policies. A tolerant reader drops the partial frame so that playback can carry on. A validator in a data pipeline has to flag it, because a silently shortened file is a corrupted input. wavguard is that validator.

## Install

```shell
pip install wavguard
```

wavguard needs Python 3.11 or newer and has no dependencies outside the standard library.

## Command line

```console
$ wavguard ok.wav
ok.wav: OK

$ wavguard cut.wav
cut.wav:36: error DATA_EXCEEDS_FILE data size 43200 exceeds the 43196 bytes present
cut.wav:36: error DATA_NOT_FRAME_ALIGNED 43196 bytes of data is not a whole number of 9-byte frames
cut.wav:4: warning RIFF_SIZE_EXCEEDS_FILE RIFF size 43236 needs 43244 bytes, but the file is 43240 bytes
cut.wav: FAILED (2 errors, 1 warning)
```

Each finding is printed as `FILE:OFFSET: SEVERITY CODE message`, with the byte offset in decimal. The last line is always a summary.

Warnings don't fail a run unless you pass `--strict`, which counts them as errors:

```console
$ wavguard trailing.wav
trailing.wav:43244: warning TRAILING_BYTES 6 bytes after the RIFF chunk, which ends at 43244
trailing.wav: OK (1 warning)

$ wavguard --strict trailing.wav
trailing.wav:43244: warning TRAILING_BYTES 6 bytes after the RIFF chunk, which ends at 43244
trailing.wav: FAILED (1 warning)
```

| Exit code | Meaning |
|-----------|---------|
| 0 | No errors. With `--strict`, no warnings either. |
| 1 | At least one error, or a warning with `--strict`. |
| 2 | Usage error, the file can't be read, or an internal error. Details go to stderr. |

A file that can be read but isn't a WAV file exits 1, because that's a finding. Exit 2 is kept for problems with the run itself, so a bug in wavguard can never look like an invalid file.

## Python API

```python
from wavguard import validate

report = validate("cut.wav")
print("ok:", report.ok)
print("frames:", report.frames_declared, "declared,", report.complete_frames, "complete")
for finding in report.errors:
    print(finding.code, finding.offset, finding.message)
```

```text
ok: False
frames: 4800 declared, 4799 complete
DATA_EXCEEDS_FILE 36 data size 43200 exceeds the 43196 bytes present
DATA_NOT_FRAME_ALIGNED 36 43196 bytes of data is not a whole number of 9-byte frames
```

`validate` accepts a path or a seekable binary file object, and takes `strict=True` for the `--strict` behaviour. It raises `OSError` for a path it can't read, `ValueError` for a file object that can't seek, and `TypeError` for a text-mode file or any other type; problems inside the file are always reported as findings, never raised. The report also includes `warnings`, `channels`, `bits_per_sample`, `sample_rate` and `frame_size`.

## What it checks

| Code | Severity | Meaning | Spec cases |
|------|----------|---------|------------|
| `NOT_RIFF_WAVE` | error | Not a RIFF/WAVE file, or shorter than the 12-byte header | L |
| `UNSUPPORTED_FORMAT` | error | RF64, BW64, RIFX or Wave64, or a format other than PCM or IEEE float | M, P |
| `NO_FMT_CHUNK` | error | No `fmt ` chunk | N |
| `NO_DATA_CHUNK` | error | No `data` chunk | O, W |
| `FMT_INVALID` | error | `fmt ` too short, or channels, sample rate, block_align or bit depth invalid | Q, W |
| `BLOCK_ALIGN_MISMATCH` | error | block_align ≠ channels × bytes per sample | K |
| `DATA_EXCEEDS_FILE` | error | Declared data size is larger than the bytes present | B, C, E |
| `DATA_NOT_FRAME_ALIGNED` | error | Data ends partway through a frame | B, D, E |
| `DATA_SIZE_ZERO_WITH_DATA` | error | Data size is 0 but audio bytes follow | H, I |
| `LIMIT_EXCEEDED` | error | More than 1,024 chunks, or a `fmt ` over 1,024 bytes | V |
| `DATA_SIZE_UNKNOWN` | warning | Data size is `0xFFFFFFFF` (a streamed file) | G |
| `RIFF_SIZE_EXCEEDS_FILE` | warning | RIFF size claims more bytes than the file has | R |
| `TRAILING_BYTES` | warning | Bytes after the end of the RIFF chunk | S |
| `ODD_CHUNK_PAD_MISSING` | warning | Odd-sized chunk without its pad byte | U |

The case letters refer to the acceptance cases in [SPEC.md](SPEC.md) §7. The spec defines every rule exactly, including the byte offset each code reports.

## Scope and limits

- Reads headers only and never decodes audio. Beyond chunk headers and the `fmt ` payload, it reads at most a few bytes, for its plausibility checks.
- Supports PCM and IEEE float, plain or WAVE_FORMAT_EXTENSIBLE.
- Reports RF64, BW64, RIFX and Wave64 as `UNSUPPORTED_FORMAT`.
- Needs seekable input: no pipes, no stdin.
- Scans at most 1,024 chunks and accepts a `fmt ` chunk of at most 1,024 bytes, so a hostile file can't make it loop or allocate without bound.
- Not a repair tool. It never writes to a file.
- Uses only the standard library. It doesn't import `soundfile` or `wave`, so it shares no code with the readers it checks.
- One file per run, with plain-text output. More than one file, `--json` and stdin aren't in v0.1.

## How readers differ

Each row is a 3-channel, 24-bit file with 4,800 frames, modified as described. Measured with libsndfile 1.2.2 through python-soundfile 0.14.0, and with the `wave` module in Python 3.12.14. Behaviour may differ in other versions.

| File | libsndfile 1.2.2 | Python `wave` | wavguard |
|------|------------------|---------------|----------|
| Cut by 4 bytes | 4,799 frames, no warning | reports 4,800, reads 4,799 | `DATA_EXCEEDS_FILE`, `DATA_NOT_FRAME_ALIGNED` |
| Cut by one whole frame | 4,799 frames, no warning | reports 4,800, reads 4,799 | `DATA_EXCEEDS_FILE` |
| Data size `0xFFFFFFFF` | 4,800 frames | reports 477,218,588, reads 4,800 | warning `DATA_SIZE_UNKNOWN` |
| Data size 0, RIFF size correct | 0 frames | 0 frames | `DATA_SIZE_ZERO_WITH_DATA` |
| Data size 0, RIFF size 8 | 4,800 frames (size recovered) | raises `wave.Error` | `DATA_SIZE_ZERO_WITH_DATA`, warning `TRAILING_BYTES` |

The readers aren't wrong here. They're built to play what they can. wavguard is built to tell you what's there.

## Development

```shell
pip install -e ".[dev]"
pytest
```

The fuzz tests run 5,000 mutations per base file. Set `WAVGUARD_FUZZ_ITERS` for longer runs:

```shell
WAVGUARD_FUZZ_ITERS=50000 pytest tests/test_fuzz.py
```

The tests marked `differential` compare frame counts with python-soundfile. They run only when `soundfile` is installed, and it isn't a dev dependency. [SPEC.md](SPEC.md) is the contract: every rule, offset and exit code the tests check is defined there.

## License

MIT. See [LICENSE](LICENSE).
