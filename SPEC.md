# wavguard — Specification v0.1

## 1. Purpose

wavguard is a strict validator for WAV files. It reports structural problems that permissive readers silently absorb, such as truncated data, partial frames and inconsistent size fields. It never repairs a file and never decodes audio.

## 2. Principles

- **Own parser, standard library only.** wavguard never imports `soundfile`, `wave` or any other audio library, so it shares no code with the readers it checks. It requires Python 3.11 or newer.
- **Headers, not audio.** It reads the RIFF header, every chunk header and the `fmt ` payload. From the `data` payload it reads at most 8 bytes, and only for the zero-size rule in §4.3. It reads no other chunk payload. It never reads audio samples.
- **Bounded work.** A hostile file cannot make wavguard loop or allocate without limit:

| Limit | Value | When exceeded |
|-------|-------|---------------|
| Chunks scanned (including `fmt ` and `data`) | 1024 | `LIMIT_EXCEEDED`; scanning stops |
| `fmt ` payload size | 1024 bytes | `LIMIT_EXCEEDED`; `fmt ` is not parsed |
| Input | must be seekable | `validate()` raises `ValueError`; the CLI exits 2 |

## 3. Scope of v0.1

**In scope:** RIFF/WAVE files with

- PCM (format tag `1`),
- IEEE float (format tag `3`),
- WAVE_FORMAT_EXTENSIBLE (format tag `0xFFFE`) whose SubFormat GUID is PCM or IEEE float. That means the first two GUID bytes are `1` or `3` (little-endian) and the other 14 are the standard suffix `00 00 00 00 10 00 80 00 00 AA 00 38 9B 71`.

**Out of scope:**

- RF64, BW64, RIFX and Wave64. They are recognised by their magic (`RF64`, `BW64`, `RIFX`, `riff`) and reported as `UNSUPPORTED_FORMAT`, never as `NOT_RIFF_WAVE`.
- Compressed and other format tags (ADPCM, µ-law, A-law, MP3 and so on), reported as `UNSUPPORTED_FORMAT`.
- Metadata chunks such as `LIST`, `bext` and `id3 `. They are skipped, not parsed.
- Repair of any kind.

## 4. Violations

Every finding has a stable **code**, a **severity** (`error` or `warning`), a **message** and an **offset**. The offset is `int | None`. The Offset column below defines it for each code. A chunk header's offset is the position of its 4-byte ID.

### 4.1 Codes

| Code | Severity | Offset | Meaning |
|------|----------|--------|---------|
| `NOT_RIFF_WAVE` | error | 0 | The file is shorter than 12 bytes, or does not start with `RIFF`…`WAVE`, and its magic is not one of the unsupported ones in §3. |
| `UNSUPPORTED_FORMAT` | error | 0 for a magic. Otherwise the format tag field (`fmt ` payload start) or the SubFormat field (`fmt ` payload start + 24). | The magic is `RF64`, `BW64`, `RIFX` or `riff`, or the format tag or SubFormat is not in scope. |
| `NO_FMT_CHUNK` | error | `None` | No `fmt ` chunk was found. |
| `NO_DATA_CHUNK` | error | `None` | No `data` chunk was found. |
| `FMT_INVALID` | error | `fmt ` chunk header | The `fmt ` payload (§4.2) is shorter than 16 bytes (40 for tag `0xFFFE`); or channels, sample rate or block_align is 0; or bits per sample is 0 or not a multiple of 8; or the format is float and bits per sample is not 32 or 64. |
| `BLOCK_ALIGN_MISMATCH` | error | `fmt ` payload start + 12 | block_align differs from the frame size (§4.2). |
| `DATA_EXCEEDS_FILE` | error | `data` chunk header | A regular declared data size (§4.2) is larger than the number of bytes from the data payload start to the end of the file. |
| `DATA_NOT_FRAME_ALIGNED` | error | `data` chunk header | Usable data bytes (§4.2) are not a whole number of frames. |
| `DATA_SIZE_ZERO_WITH_DATA` | error | *p* (§4.3) | The declared data size is 0 but data bytes follow (rule in §4.3). |
| `LIMIT_EXCEEDED` | error | Header of the chunk that tripped the limit | A limit from §2 was hit. |
| `DATA_SIZE_UNKNOWN` | warning | `data` chunk header | The declared data size is `0xFFFFFFFF` (a streamed file whose size was never written back). |
| `RIFF_SIZE_EXCEEDS_FILE` | warning | 4 | 8 + RIFF size > file size, which suggests truncation. |
| `TRAILING_BYTES` | warning | 8 + RIFF size | file size > 8 + RIFF size: there are bytes after the end of the RIFF chunk. |
| `ODD_CHUNK_PAD_MISSING` | warning | *q* (§4.3) | An odd-sized chunk is not followed by its pad byte (rule in §4.3). |

A RIFF size of `0xFFFFFFFF` means unknown. Neither RIFF size warning fires for it.

### 4.2 Definitions and check order

**Frame size** is `channels × bits_per_sample / 8`. It is the only frame size used for alignment checks and frame counts. block_align is never used in calculations. It is only compared with the frame size, through `BLOCK_ALIGN_MISMATCH`.

Checks run in this order:

1. **Header.** If the file is shorter than 12 bytes, report `NOT_RIFF_WAVE` and stop. If the magic is unsupported, report `UNSUPPORTED_FORMAT` and stop. If the file isn't `RIFF`…`WAVE`, report `NOT_RIFF_WAVE` and stop. Otherwise compare the RIFF size with the file size (two warnings in §4.1).
2. **Chunk scan.** Start at offset 12 and continue to the end of the file, not to the end of the RIFF chunk, so a wrong RIFF size cannot hide chunks. Each header is a 4-byte ID followed by a little-endian 32-bit size. The next header sits at payload start + size, plus 1 if the size is odd (subject to the pad rule in §4.3). Only the first `fmt ` and the first `data` chunk are used. Scanning stops when:
   - fewer than 8 bytes remain,
   - the next ID is not plausible (§4.3),
   - any chunk, including `fmt ` or `data`, runs past the end of the file (that chunk is still checked),
   - the `data` size is unknown, or
   - the chunk limit is reached.
3. **`fmt `.** The payload is `min(declared size, bytes to the end of the file)`. Checks run in this order, and the first finding ends the `fmt ` checks:
   - Declared size over 1024: `LIMIT_EXCEEDED`.
   - Payload under 16 bytes: `FMT_INVALID`, before any field is read.
   - Read the format tag. If it is `0xFFFE` and the payload is under 40 bytes: `FMT_INVALID`.
   - Format check (the tag, then the SubFormat for `0xFFFE`): `UNSUPPORTED_FORMAT`.
   - Field checks: `FMT_INVALID`.
   - Compute the frame size and compare block_align with it.
4. **`data`.** Classify the declared size first:
   - `0xFFFFFFFF`: report `DATA_SIZE_UNKNOWN`. Usable bytes are everything to the end of the file. There is no exceeds check.
   - `0`: apply the zero-size rule (§4.3). Usable bytes are 0.
   - Anything else (regular): available = file size − payload start. Report `DATA_EXCEEDS_FILE` if declared > available. Usable bytes = `min(declared, available)`.

   Then report `DATA_NOT_FRAME_ALIGNED` if usable bytes are not divisible by the frame size.
5. **Missing chunks.** Report `NO_FMT_CHUNK` or `NO_DATA_CHUNK` once the scan ends, unless the scan stopped on `LIMIT_EXCEEDED`. In that case the chunk may exist beyond the limit, so absence is not reported.

If `fmt ` is missing, invalid, unsupported or over the size limit, every check that needs the frame size is skipped, and the frame values in the report are `None`.

### 4.3 Plausibility rules

A **plausible chunk ID** is four bytes, each in the range `0x20`–`0x7E` (printable ASCII).

- **Zero data size.** Let *p* be the data payload start. If no bytes remain after *p*, there is no finding. If at least 8 bytes remain and the four at *p* form a plausible ID, they are the next chunk header. In every other case, report `DATA_SIZE_ZERO_WITH_DATA` at offset *p* and stop scanning. Those cases are: 1–7 bytes remain, or the four bytes at *p* are not a plausible ID.
- **Missing pad byte.** For a chunk with odd size *n* and payload start *p*, let *q* = *p* + *n*.
  - If *q* is the end of the file, the pad is missing.
  - Otherwise the pad is missing if the four bytes at *q* + 1 are not a plausible ID but the four at *q* are. Scanning then continues at *q*.
  - In all other cases the next header is at *q* + 1.

  The finding's offset is *q*.

These rules are heuristics. Audio whose first four bytes happen to be printable ASCII will be read as a chunk header.

### 4.4 Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Is block_align ≠ frame size an error? | **Error.** | For PCM, libsndfile ignores block_align (observed with libsndfile 1.2.2 and master). That is the kind of leniency this tool exists to flag. |
| Is a missing odd-chunk pad byte an error? | **Warning.** | Odd-sized chunks and pad bytes are a long-standing source of confusion (see libsndfile #887), and many real files omit the pad. |
| Is a 0 data size with bytes after it (case H) an error? | **Error.** | libsndfile repairs it automatically. wavguard is strict. |
| RF64 in v0.1? | **No. Reported as unsupported.** | RF64 uses a different size layout. It is left for a later version. |
| Does the chunk scan stop at the RIFF end or at the end of the file? | **End of file.** | A wrong RIFF size is already a warning. It should not also hide the `fmt ` or `data` chunk (case H). |
| Zero data size followed by 1–7 bytes? | **Error.** | Those bytes cannot be a chunk header, so they can only be data. |
| Report missing chunks after `LIMIT_EXCEEDED`? | **No.** | Their absence is unknown, not proven. |
| Minimum Python version | **3.11** | 3.10 reaches end of life in October 2026 (PEP 619). Confirm on the devguide's status-of-versions page before release. |

## 5. Result and interface

### Python API

```python
validate(source, *, strict=False) -> Report
```

`source` is a `str`, an `os.PathLike` or a seekable binary file object. I/O failures raise `OSError`, and a file object that can't seek raises `ValueError`. Malformed or hostile content never raises: it is reported as findings.

| `Report` field | Type | Meaning |
|----------------|------|---------|
| `ok` | `bool` | No errors. With `strict=True`, no warnings either. |
| `errors` | `list[Finding]` | Error findings, in file order |
| `warnings` | `list[Finding]` | Warning findings, in file order |
| `channels`, `bits_per_sample`, `sample_rate` | `int \| None` | From `fmt ` |
| `frame_size` | `int \| None` | §4.2 |
| `frames_declared` | `int \| None` | declared data size ÷ frame size, rounded down. `None` when the size is unknown. |
| `complete_frames` | `int \| None` | usable bytes ÷ frame size, rounded down |

`Finding` has `code`, `severity`, `message` and `offset: int | None`.

### Command line

```
wavguard [--strict] FILE
```

It prints one finding per line, `<offset> <severity> <code> <message>`. The offset is in decimal, or `-` when it is `None`. `--strict` calls `validate(..., strict=True)`, so warnings count as errors.

| Exit code | Meaning |
|-----------|---------|
| 0 | `ok` |
| 1 | Errors found (or warnings, with `--strict`) |
| 2 | Usage or I/O problem |

## 6. Non-goals

- Repair, or writing to the file in any way
- Decoding or analysing audio (samples, silence, clipping, loudness)
- RF64, BW64, RIFX and Wave64 in v0.1
- Parsing metadata chunks (`LIST`, `bext`, `id3 `, cue points)
- Compressed formats
- Plugins or a format registry
- Validating non-seekable streams
- Checking chunk order or duplicate chunks: `fmt ` after `data` is accepted, and later duplicates are ignored

## 7. Acceptance cases

Rules:

- Every code in §4.1 is produced by at least one case.
- The error and warning sets listed below are exact.

Fixture constraints, so each case tests what it claims:

- **B–E, K** need a frame size that doesn't divide 4 and isn't 8, so a 4-byte cut breaks alignment and block_align 8 is wrong. For example 24-bit stereo, which has 6-byte frames.
- **T, U** need an odd frame size, so odd-length data is still frame-aligned. For example 24-bit mono, which has 3-byte frames.
- **H, I, S:** the bytes after the data header (H, I) or after the RIFF end (S) must not start with a plausible chunk ID. Zeros work.
- **U:** the `LIST` chunk's size must be under `0x20`. Then its first size byte isn't printable, and the pad heuristic can't see a plausible ID at both *q* and *q* + 1.
- **RIFF size:** in B, C, E and W it is left as it was. Where a case says "patched", it is patched. In every other case it matches the file.

| Case | File | Errors | Warnings |
|------|------|--------|----------|
| A | Intact PCM | — | — |
| B | A cut by 4 bytes | `DATA_EXCEEDS_FILE`, `DATA_NOT_FRAME_ALIGNED` | `RIFF_SIZE_EXCEEDS_FILE` |
| C | A cut by one whole frame | `DATA_EXCEEDS_FILE` | `RIFF_SIZE_EXCEEDS_FILE` |
| D | A cut by 4 bytes, data and RIFF sizes patched | `DATA_NOT_FRAME_ALIGNED` | — |
| E | Intact EXTENSIBLE cut by 4 bytes | `DATA_EXCEEDS_FILE`, `DATA_NOT_FRAME_ALIGNED` | `RIFF_SIZE_EXCEEDS_FILE` |
| F | Intact EXTENSIBLE | — | — |
| G | A with data size `0xFFFFFFFF` | — | `DATA_SIZE_UNKNOWN` |
| H | A with data size 0 and RIFF size 8 | `DATA_SIZE_ZERO_WITH_DATA` | `TRAILING_BYTES` |
| I | A with data size 0 | `DATA_SIZE_ZERO_WITH_DATA` | — |
| J | A plus a trailing `LIST` chunk | — | — |
| K | A with block_align 8 | `BLOCK_ALIGN_MISMATCH` | — |
| L | Empty file; a 5-byte file | `NOT_RIFF_WAVE` | — |
| M | RF64 magic | `UNSUPPORTED_FORMAT` | — |
| N | No `fmt ` chunk | `NO_FMT_CHUNK` | — |
| O | No `data` chunk | `NO_DATA_CHUNK` | — |
| P | Format tag 2 (ADPCM) | `UNSUPPORTED_FORMAT` | — |
| Q | `fmt ` with 0 channels | `FMT_INVALID` (and no crash) | — |
| R | A with RIFF size larger than the file | — | `RIFF_SIZE_EXCEEDS_FILE` |
| S | A plus bytes appended after the RIFF end | — | `TRAILING_BYTES` |
| T | Odd-length data, with its pad byte, before a `LIST` chunk | — | — |
| U | Same as T without the pad byte | — | `ODD_CHUNK_PAD_MISSING` |
| V | `fmt `, `data`, then 5000 empty `JUNK` chunks | `LIMIT_EXCEEDED` | — |
| W | A cut 10 bytes into the `fmt ` payload | `FMT_INVALID`, `NO_DATA_CHUNK` | `RIFF_SIZE_EXCEEDS_FILE` |
