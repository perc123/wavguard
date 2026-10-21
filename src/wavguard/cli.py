import argparse
import sys
from collections.abc import Sequence

from wavguard import __version__
from wavguard.core import validate
from wavguard.model import Finding, Report

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TROUBLE = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wavguard",
        description="Strict validator for WAV files. Reports problems; never repairs.",
    )
    parser.add_argument("--strict", action="store_true", help="count warnings as errors")
    parser.add_argument("--version", action="version", version=f"wavguard {__version__}")
    parser.add_argument("file", metavar="FILE", help="WAV file to check")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_TROUBLE

    try:
        return _run(args.file, args.strict)
    except Exception as exc:  # noqa: BLE001
        # A bug must never exit 1, which means "the file has findings".
        print(f"wavguard: internal error: {exc!r}", file=sys.stderr)
        return EXIT_TROUBLE


def _run(path: str, strict: bool) -> int:
    try:
        with open(path, "rb") as f:
            if not f.seekable():
                print(f"wavguard: cannot read {path}: not seekable", file=sys.stderr)
                return EXIT_TROUBLE
            report = validate(f, strict=strict)
    except OSError as exc:
        print(f"wavguard: cannot read {path}: {exc.strerror or exc}", file=sys.stderr)
        return EXIT_TROUBLE

    lines = [_finding_line(path, finding) for finding in report.errors + report.warnings]
    lines.append(_summary_line(path, report))
    print("\n".join(lines))
    return EXIT_OK if report.ok else EXIT_FINDINGS


def _finding_line(path: str, finding: Finding) -> str:
    location = path if finding.offset is None else f"{path}:{finding.offset}"
    return f"{location}: {finding.severity} {finding.code} {finding.message}"


def _summary_line(path: str, report: Report) -> str:
    counts = [
        _count(len(report.errors), "error"),
        _count(len(report.warnings), "warning"),
    ]
    status = "OK" if report.ok else "FAILED"
    listed = [c for c in counts if c]
    return f"{path}: {status} ({', '.join(listed)})" if listed else f"{path}: {status}"


def _count(n: int, noun: str) -> str:
    if n == 0:
        return ""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
