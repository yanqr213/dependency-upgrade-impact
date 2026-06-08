"""Public analysis pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .models import AnalysisResult, AnalysisSummary, DependencyChange, DependencyRecord, normalize_name
from .parsers import dedupe_records, parse_directory
from .risk import risk_level, score_change
from .scanner import scan_usage, suggest_tests
from .semver import classify_change


def analyze(
    before_path: str,
    after_path: str,
    source_root: Optional[str] = None,
    fail_on: str = "high",
    min_score: Optional[int] = None,
) -> AnalysisResult:
    """Analyze dependency changes between two directories or files."""

    before_records, before_sources = parse_directory(before_path)
    after_records, after_sources = parse_directory(after_path)
    raw_changes = compare_records(before_records, after_records)

    usage_by_key: Dict[str, List[str]] = {}
    if source_root:
        usage_by_key = scan_usage(source_root, raw_changes)

    changes: List[DependencyChange] = []
    before_map = to_record_map(before_records)
    after_map = to_record_map(after_records)
    for change in raw_changes:
        usage_files = usage_by_key.get(change.key, [])
        before = before_map.get(change.key)
        after = after_map.get(change.key)
        score, reasons, engine_hints, script_hints = score_change(
            change.change_type, change.semver, before, after, usage_files
        )
        tests = suggest_tests(source_root, usage_files) if source_root else []
        changes.append(
            DependencyChange(
                name=change.name,
                ecosystem=change.ecosystem,
                change_type=change.change_type,
                before_version=change.before_version,
                after_version=change.after_version,
                scope=change.scope,
                direct=change.direct,
                parent=change.parent,
                semver=change.semver,
                risk_score=score,
                risk_level=risk_level(score),
                reasons=reasons,
                usage_files=usage_files,
                suggested_tests=tests,
                before_license=before.license if before else None,
                after_license=after.license if after else None,
                engine_hints=engine_hints,
                script_hints=script_hints,
            )
        )

    changes.sort(key=lambda item: (-item.risk_score, item.ecosystem, normalize_name(item.name)))
    summary = summarize(changes, fail_on=fail_on, min_score=min_score)
    return AnalysisResult(
        changes=changes,
        summary=summary,
        source_files=sorted(set(before_sources + after_sources)),
        warnings=[],
    )


def compare_records(
    before_records: Iterable[DependencyRecord], after_records: Iterable[DependencyRecord]
) -> List[DependencyChange]:
    before = to_record_map(dedupe_records(before_records))
    after = to_record_map(dedupe_records(after_records))
    changes: List[DependencyChange] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        current = new or old
        assert current is not None
        if old and new:
            if old.version == new.version:
                continue
            semver = classify_change(old.version, new.version)
            changes.append(
                DependencyChange(
                    name=new.name,
                    ecosystem=new.ecosystem,
                    change_type="updated",
                    before_version=old.version,
                    after_version=new.version,
                    scope=new.scope,
                    direct=new.direct,
                    parent=new.parent,
                    semver=semver,
                )
            )
        elif new:
            changes.append(
                DependencyChange(
                    name=new.name,
                    ecosystem=new.ecosystem,
                    change_type="added",
                    after_version=new.version,
                    scope=new.scope,
                    direct=new.direct,
                    parent=new.parent,
                    semver="unknown",
                )
            )
        elif old:
            changes.append(
                DependencyChange(
                    name=old.name,
                    ecosystem=old.ecosystem,
                    change_type="removed",
                    before_version=old.version,
                    scope=old.scope,
                    direct=old.direct,
                    parent=old.parent,
                    semver="unknown",
                )
            )
    return changes


def summarize(
    changes: Iterable[DependencyChange], fail_on: str = "high", min_score: Optional[int] = None
) -> AnalysisSummary:
    items = list(changes)
    high = sum(1 for item in items if item.risk_level == "high")
    medium = sum(1 for item in items if item.risk_level == "medium")
    low = sum(1 for item in items if item.risk_level == "low")
    max_score = max((item.risk_score for item in items), default=0)
    gate_failed = should_fail(items, fail_on, min_score)
    return AnalysisSummary(
        total_changes=len(items),
        added=sum(1 for item in items if item.change_type == "added"),
        removed=sum(1 for item in items if item.change_type == "removed"),
        updated=sum(1 for item in items if item.change_type == "updated"),
        major=sum(1 for item in items if item.semver == "major"),
        minor=sum(1 for item in items if item.semver == "minor"),
        patch=sum(1 for item in items if item.semver == "patch"),
        high_risk=high,
        medium_risk=medium,
        low_risk=low,
        max_risk_score=max_score,
        gate_failed=gate_failed,
    )


def should_fail(
    changes: Iterable[DependencyChange], fail_on: str = "high", min_score: Optional[int] = None
) -> bool:
    items = list(changes)
    if min_score is not None and any(item.risk_score >= min_score for item in items):
        return True
    level_order = {"none": 99, "low": 1, "medium": 2, "high": 3}
    threshold = level_order.get(fail_on, 3)
    if threshold == 99:
        return False
    return any(level_order.get(item.risk_level, 1) >= threshold for item in items)


def to_record_map(records: Iterable[DependencyRecord]) -> Dict[str, DependencyRecord]:
    return {record.key: record for record in records}
