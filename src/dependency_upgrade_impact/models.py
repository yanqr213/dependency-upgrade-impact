"""Core data structures for dependency upgrade impact analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DependencyRecord:
    """A dependency found in a manifest or lock file."""

    name: str
    version: str = ""
    ecosystem: str = "unknown"
    source: str = ""
    scope: str = "runtime"
    direct: bool = True
    parent: Optional[str] = None
    license: Optional[str] = None
    engines: Dict[str, str] = field(default_factory=dict)
    scripts: Dict[str, str] = field(default_factory=dict)
    specifier: str = ""

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{normalize_name(self.name)}"


@dataclass(frozen=True)
class DependencyChange:
    """A before/after change for one dependency."""

    name: str
    ecosystem: str
    change_type: str
    before_version: str = ""
    after_version: str = ""
    scope: str = "runtime"
    direct: bool = True
    parent: Optional[str] = None
    semver: str = "unknown"
    risk_score: int = 0
    risk_level: str = "low"
    reasons: List[str] = field(default_factory=list)
    usage_files: List[str] = field(default_factory=list)
    suggested_tests: List[str] = field(default_factory=list)
    before_license: Optional[str] = None
    after_license: Optional[str] = None
    engine_hints: List[str] = field(default_factory=list)
    script_hints: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{normalize_name(self.name)}"


@dataclass(frozen=True)
class AnalysisSummary:
    total_changes: int
    added: int
    removed: int
    updated: int
    major: int
    minor: int
    patch: int
    high_risk: int
    medium_risk: int
    low_risk: int
    max_risk_score: int
    gate_failed: bool


@dataclass(frozen=True)
class AnalysisResult:
    changes: List[DependencyChange]
    summary: AnalysisSummary
    source_files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def normalize_name(name: str) -> str:
    """Normalize names while preserving npm scope content."""

    return name.strip().lower().replace("_", "-")
