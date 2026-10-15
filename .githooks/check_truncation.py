#!/usr/bin/env python3
"""
Guard against silently truncated / corrupted text files.

The workspace file mount has occasionally handed back stale or partially-flushed copies of a file; saving one back drops its tail.
Code is protected by the test suite (truncation throws a SyntaxError), but docs and config are not.
This script catches the two reliable symptoms before they get committed:

  1. NUL bytes in a text file (a sign of a bad encoding / partial write), and
  2. a tracked file that shrank by more than a threshold vs HEAD (the signal that
     actually catches a truncated doc — a lost tail is a big size drop).

Usage:
    python .githooks/check_truncation.py            # check staged files (pre-commit)
    python .githooks/check_truncation.py --all      # check all tracked text files

Exit code is non-zero if any problem is found, so it works as a pre-commit hook or a CI step.

A large deletion can be acknowledged three ways:
  * `--allow-shrink <path>` (repeatable) -- CLI / CI,
  * env var TRUNCATION_ALLOW_SHRINK (comma-separated paths) -- CLI / CI, or
  * the .truncation-allow file -- GUI-friendly. When a shrink trips the check, each
    suspect is written into this (gitignored) file commented out; uncomment the
    line(s) you meant to shrink and re-commit. The file is deleted once the check
    passes, so the acknowledgment is one-shot rather than a standing exemption.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# Extensions we treat as text worth checking for shrink/NUL.
TEXT_SUFFIXES: set[str] = {".py", ".md", ".json", ".toml", ".cfg", ".ini", ".txt", ".yaml", ".yml"}

# Flag a tracked file that lost more than this fraction of its bytes vs HEAD.
SHRINK_THRESHOLD: float = 0.40

# Generated files that legitimately shrink between builds.
GENERATED_PREFIXES: tuple[str, ...] = ("packages/femforge-workbench/src/femforge_workbench/widgets/serve/",)

# GUI-friendly acknowledgment file. Written (commented out) when a shrink trips the
# check, read for uncommented paths to allow, and deleted once the check passes.
ALLOW_FILE: Path = Path(".truncation-allow")

_ALLOW_FILE_HEADER: tuple[str, ...] = (
    "# Intentional large shrinks to acknowledge for the truncation guard.",
    "# Uncomment a path below to allow it past the shrink check, then re-commit.",
    "# This file is gitignored and auto-deleted once the check passes, so each",
    "# acknowledgment is one-shot rather than a standing exemption.",
    "",
)


def read_allow_file() -> set[str]:
    """Return the uncommented (active) paths acknowledged in ``ALLOW_FILE``."""
    if not ALLOW_FILE.is_file():
        return set()
    active: set[str] = set()
    for line in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        active.add(stripped)
    return active


def write_allow_file(suspects: set[str], active: set[str]) -> None:
    """Refresh ``ALLOW_FILE`` listing every shrink suspect.

    Paths the user already uncommented (``active``) stay uncommented; the rest are
    written commented out, ready to be uncommented from an editor and re-committed.
    """
    lines: list[str] = list(_ALLOW_FILE_HEADER)
    for path in sorted(suspects | active):
        lines.append(path if path in active else f"# {path}")
    ALLOW_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


def staged_files() -> list[str]:
    out: str = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"])
    return [line for line in out.splitlines() if line.strip()]


def all_tracked_files() -> list[str]:
    out: str = _run(["git", "ls-files"])
    return [line for line in out.splitlines() if line.strip()]


def head_size(path: str) -> int | None:
    res = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, check=False)
    if res.returncode != 0:
        return None  # not in HEAD (new file)
    return len(res.stdout)


def check_file(path: str, allow_shrink: set[str]) -> list[tuple[str, str]]:
    """Return ``(kind, message)`` problems for ``path`` (empty if clean).

    ``kind`` is ``"nul"`` for corruption (never acknowledgeable) or ``"shrink"``
    for a large size drop (acknowledgeable via the allow file / flag / env var).
    """
    p = Path(path)
    if p.suffix not in TEXT_SUFFIXES or not p.is_file() or path.startswith(GENERATED_PREFIXES):
        return []

    problems: list[tuple[str, str]] = []
    raw: bytes = p.read_bytes()

    if b"\x00" in raw:
        problems.append(("nul", "contains NUL byte(s) — likely a corrupt/partial write"))
        return problems

    if path not in allow_shrink:
        prior: int | None = head_size(path)
        if prior is not None and prior > 0:
            shrink: float = 1.0 - (len(raw) / prior)
            if shrink > SHRINK_THRESHOLD:
                problems.append(
                    (
                        "shrink",
                        f"shrank {shrink:.0%} vs HEAD ({prior} -> {len(raw)} bytes) — verify it wasn't truncated (intentional? acknowledge it in {ALLOW_FILE})",
                    )
                )

    return problems


def main(argv: list[str]) -> int:
    check_all: bool = "--all" in argv
    allow_shrink: set[str] = set()
    for i, a in enumerate(argv):
        if a == "--allow-shrink" and i + 1 < len(argv):
            allow_shrink.add(argv[i + 1])
    allow_shrink |= {s.strip() for s in os.environ.get("TRUNCATION_ALLOW_SHRINK", "").split(",") if s.strip()}

    active: set[str] = read_allow_file()
    allow_shrink |= active

    files: list[str] = all_tracked_files() if check_all else staged_files()

    failures: dict[str, list[str]] = {}
    shrink_suspects: set[str] = set()
    for path in files:
        problems = check_file(path, allow_shrink)
        if problems:
            failures[path] = [msg for _, msg in problems]
            shrink_suspects |= {path for kind, _ in problems if kind == "shrink"}

    if failures:
        print("Truncation/corruption check FAILED:\n")
        for path, messages in failures.items():
            for message in messages:
                print(f"  {path}: {message}")
        if shrink_suspects or active:
            write_allow_file(shrink_suspects, active)
            print(
                f"\nA common cause is the editor/shell handing back a stale copy — re-read the\n"
                f"file and verify its last line and length before committing. If a shrink is\n"
                f"intentional, uncomment its path in {ALLOW_FILE} and re-commit (works from the\n"
                f"VS Code GUI — no terminal needed). The file is deleted once the check passes."
            )
        else:
            print("\nA common cause is the editor/shell handing back a stale copy — re-read the\nfile and verify its last line and length before committing.")
        return 1

    # Clean run: drop any one-shot acknowledgments so they don't linger.
    ALLOW_FILE.unlink(missing_ok=True)
    print(f"Truncation check passed ({len(files)} file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
