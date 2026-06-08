"""Small semantic-version helpers with no third-party dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


_VERSION_RE = re.compile(
    r"^[^\d]*(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0


def parse_version(value: str) -> Optional[Version]:
    if not value:
        return None
    text = value.strip().strip('"').strip("'")
    if text.startswith(("file:", "git+", "http://", "https://", "workspace:", "link:")):
        return None
    match = _VERSION_RE.match(text)
    if not match:
        return None
    return Version(
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        patch=int(match.group("patch") or 0),
    )


def classify_change(before: str, after: str) -> str:
    """Return major/minor/patch/downgrade/same/unknown for two versions."""

    old = parse_version(before)
    new = parse_version(after)
    if old is None or new is None:
        return "unknown"
    if (new.major, new.minor, new.patch) == (old.major, old.minor, old.patch):
        return "same"
    if (new.major, new.minor, new.patch) < (old.major, old.minor, old.patch):
        return "downgrade"
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    if new.patch != old.patch:
        return "patch"
    return "unknown"
