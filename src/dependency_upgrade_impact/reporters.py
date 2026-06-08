"""Report rendering."""

from __future__ import annotations

import json
from dataclasses import asdict
from html import escape
from typing import Iterable

from .models import AnalysisResult, DependencyChange


def to_json(result: AnalysisResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def to_markdown(result: AnalysisResult) -> str:
    lines = [
        "# Dependency Upgrade Impact Report",
        "",
        "## 摘要 / Summary",
        "",
        f"- 总变更: {result.summary.total_changes}",
        f"- 新增/移除/更新: {result.summary.added}/{result.summary.removed}/{result.summary.updated}",
        f"- SemVer major/minor/patch: {result.summary.major}/{result.summary.minor}/{result.summary.patch}",
        f"- 风险 high/medium/low: {result.summary.high_risk}/{result.summary.medium_risk}/{result.summary.low_risk}",
        f"- 最高分: {result.summary.max_risk_score}",
        f"- CI Gate: {'FAILED' if result.summary.gate_failed else 'PASSED'}",
        "",
        "## 变更详情 / Changes",
        "",
    ]
    if not result.changes:
        lines.append("未发现支持文件中的依赖变化。")
        return "\n".join(lines) + "\n"

    for change in result.changes:
        direct = "direct" if change.direct else "transitive"
        lines.extend(
            [
                f"### {change.ecosystem}:{change.name}",
                "",
                f"- 类型: {change.change_type}",
                f"- 版本: `{change.before_version or '-'}` -> `{change.after_version or '-'}`",
                f"- SemVer: {change.semver}",
                f"- 范围: {change.scope}, {direct}",
                f"- 风险: {change.risk_level} ({change.risk_score})",
            ]
        )
        if change.before_license or change.after_license:
            lines.append(f"- 许可: `{change.before_license or '-'}` -> `{change.after_license or '-'}`")
        if change.reasons:
            lines.append("- 原因: " + "；".join(change.reasons))
        if change.engine_hints:
            lines.append("- Engine: " + ", ".join(f"`{item}`" for item in change.engine_hints))
        if change.script_hints:
            lines.append("- 安装脚本: " + ", ".join(f"`{item}`" for item in change.script_hints))
        if change.usage_files:
            lines.append("- 受影响源码: " + ", ".join(f"`{item}`" for item in change.usage_files))
        if change.suggested_tests:
            lines.append("- 建议测试: " + ", ".join(f"`{item}`" for item in change.suggested_tests))
        lines.append("")
    return "\n".join(lines)


def to_junit(result: AnalysisResult) -> str:
    failures = [change for change in result.changes if change.risk_level == "high"]
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<testsuite name="dependency-upgrade-impact" tests="{len(result.changes)}" failures="{len(failures)}">',
    ]
    for change in result.changes:
        case_name = f"{change.ecosystem}:{change.name}"
        lines.append(f'  <testcase classname="dependency_upgrade_impact" name="{escape(case_name)}">')
        if change.risk_level == "high":
            message = f"{change.change_type} {change.before_version}->{change.after_version} score={change.risk_score}"
            lines.append(f'    <failure message="{escape(message)}">{escape("; ".join(change.reasons))}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def render(result: AnalysisResult, fmt: str) -> str:
    if fmt == "json":
        return to_json(result)
    if fmt == "junit":
        return to_junit(result)
    if fmt == "markdown":
        return to_markdown(result)
    raise ValueError(f"unsupported format: {fmt}")
