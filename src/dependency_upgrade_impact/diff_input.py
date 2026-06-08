"""Parse simple unified diffs as an alternate input source."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Tuple


SUPPORTED_NAMES = {"package.json", "package-lock.json", "requirements.txt", "pyproject.toml"}


def materialize_diff(diff_path: str) -> Tuple[str, str]:
    """Create before/after directories from a unified diff.

    This is intentionally conservative. It supports file additions/removals in ordinary unified
    diffs and preserves context lines. It is enough for CI artifacts produced by
    `git diff HEAD~1 -- package.json requirements.txt`.
    """

    before_dir = tempfile.mkdtemp(prefix="dui-before-")
    after_dir = tempfile.mkdtemp(prefix="dui-after-")
    before_files, after_files = parse_unified_diff(Path(diff_path).read_text(encoding="utf-8"))
    for name, lines in before_files.items():
        if Path(name).name in SUPPORTED_NAMES:
            write_text(Path(before_dir) / Path(name).name, lines)
    for name, lines in after_files.items():
        if Path(name).name in SUPPORTED_NAMES:
            write_text(Path(after_dir) / Path(name).name, lines)
    return before_dir, after_dir


def parse_unified_diff(text: str):
    before = {}
    after = {}
    old_name = None
    new_name = None
    old_lines = []
    new_lines = []
    in_hunk = False

    def flush():
        if old_name and old_name != "/dev/null":
            before[old_name] = old_lines.copy()
        if new_name and new_name != "/dev/null":
            after[new_name] = new_lines.copy()

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            old_name = None
            new_name = None
            old_lines = []
            new_lines = []
            in_hunk = False
            parts = line.split()
            if len(parts) >= 4:
                old_name = strip_prefix(parts[2])
                new_name = strip_prefix(parts[3])
            continue
        if line.startswith("--- "):
            old_name = strip_prefix(line[4:].strip())
            continue
        if line.startswith("+++ "):
            new_name = strip_prefix(line[4:].strip())
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            new_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            old_lines.append(line[1:])
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line == r"\ No newline at end of file":
            continue
    flush()
    return before, after


def strip_prefix(value: str) -> str:
    value = value.strip()
    if value == "/dev/null":
        return value
    for prefix in ("a/", "b/"):
        if value.startswith(prefix):
            return value[2:]
    return value


def write_text(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cleanup_materialized(paths: Iterable[str]) -> None:
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
