"""Report rendering."""

from __future__ import annotations

import json
from dataclasses import asdict
from html import escape

from .models import AnalysisResult

SUPPORTED_LANGUAGES = ("zh", "en")

LABELS = {
    "zh": {
        "summary": "摘要 / Summary",
        "total_changes": "总变更",
        "change_counts": "新增/移除/更新",
        "risk_counts": "风险 high/medium/low",
        "max_score": "最高分",
        "changes": "变更详情 / Changes",
        "no_changes": "未发现支持文件中的依赖变化。",
        "type": "类型",
        "version": "版本",
        "scope": "范围",
        "risk": "风险",
        "license": "许可",
        "reasons": "原因",
        "scripts": "安装脚本",
        "usage": "受影响源码",
        "tests": "建议测试",
    },
    "en": {
        "summary": "Summary",
        "total_changes": "Total changes",
        "change_counts": "Added/removed/updated",
        "risk_counts": "Risk high/medium/low",
        "max_score": "Max score",
        "changes": "Changes",
        "no_changes": "No dependency changes were found in supported files.",
        "type": "Type",
        "version": "Version",
        "scope": "Scope",
        "risk": "Risk",
        "license": "License",
        "reasons": "Reasons",
        "scripts": "Install scripts",
        "usage": "Affected source",
        "tests": "Suggested tests",
    },
}

REASON_TRANSLATIONS = {
    "新增依赖需要确认供应链与许可": "Added dependency requires supply-chain and license review",
    "移除依赖可能导致运行时缺失": "Removed dependency may cause a runtime dependency gap",
    "依赖版本发生更新": "Dependency version changed",
    "major 升级通常包含破坏性变更": "Major upgrades often include breaking changes",
    "minor 升级可能引入新行为": "Minor upgrades may introduce new behavior",
    "patch 升级风险较低但仍需验证": "Patch upgrades are lower risk but should still be validated",
    "版本降级可能回退安全或兼容性修复": "Downgrades may remove security or compatibility fixes",
    "版本格式无法判断 semver 风险": "Version format cannot be classified with SemVer",
    "间接依赖变化可能来自上游依赖树": "Transitive dependency change may come from an upstream dependency tree",
    "源码中发现 import/require 使用": "Source imports or require calls reference this dependency",
    "未发现源码直接使用，仍可能通过插件或动态加载使用": "No direct source usage was found, but plugins or dynamic loading may still use it",
    "许可信息发生变化": "License metadata changed",
    "依赖声明了运行时 engine 约束": "Dependency declares a runtime engine constraint",
    "依赖包含安装期脚本": "Dependency includes install-time scripts",
}


def normalize_language(language: str) -> str:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language}")
    return language


def translate_reason(reason: str, language: str) -> str:
    if language == "zh":
        return reason
    if reason.startswith("许可可能需要人工复核: "):
        value = reason.split(": ", 1)[1]
        return f"License may need human review: {value}"
    return REASON_TRANSLATIONS.get(reason, reason)


def result_to_dict(result: AnalysisResult, language: str = "zh") -> dict:
    language = normalize_language(language)
    data = asdict(result)
    if language == "en":
        for change in data["changes"]:
            change["reasons"] = [translate_reason(item, language) for item in change["reasons"]]
    return data


def to_json(result: AnalysisResult, language: str = "zh") -> str:
    return json.dumps(result_to_dict(result, language), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def to_markdown(result: AnalysisResult, language: str = "zh") -> str:
    language = normalize_language(language)
    labels = LABELS[language]
    lines = [
        "# Dependency Upgrade Impact Report",
        "",
        f"## {labels['summary']}",
        "",
        f"- {labels['total_changes']}: {result.summary.total_changes}",
        f"- {labels['change_counts']}: {result.summary.added}/{result.summary.removed}/{result.summary.updated}",
        f"- SemVer major/minor/patch: {result.summary.major}/{result.summary.minor}/{result.summary.patch}",
        f"- {labels['risk_counts']}: {result.summary.high_risk}/{result.summary.medium_risk}/{result.summary.low_risk}",
        f"- {labels['max_score']}: {result.summary.max_risk_score}",
        f"- CI Gate: {'FAILED' if result.summary.gate_failed else 'PASSED'}",
        "",
        f"## {labels['changes']}",
        "",
    ]
    if not result.changes:
        lines.append(labels["no_changes"])
        return "\n".join(lines) + "\n"

    for change in result.changes:
        direct = "direct" if change.direct else "transitive"
        lines.extend(
            [
                f"### {change.ecosystem}:{change.name}",
                "",
                f"- {labels['type']}: {change.change_type}",
                f"- {labels['version']}: `{change.before_version or '-'}` -> `{change.after_version or '-'}`",
                f"- SemVer: {change.semver}",
                f"- {labels['scope']}: {change.scope}, {direct}",
                f"- {labels['risk']}: {change.risk_level} ({change.risk_score})",
            ]
        )
        if change.before_license or change.after_license:
            lines.append(f"- {labels['license']}: `{change.before_license or '-'}` -> `{change.after_license or '-'}`")
        if change.reasons:
            reason_separator = "；" if language == "zh" else "; "
            reasons = [translate_reason(item, language) for item in change.reasons]
            lines.append(f"- {labels['reasons']}: " + reason_separator.join(reasons))
        if change.engine_hints:
            lines.append("- Engine: " + ", ".join(f"`{item}`" for item in change.engine_hints))
        if change.script_hints:
            lines.append(f"- {labels['scripts']}: " + ", ".join(f"`{item}`" for item in change.script_hints))
        if change.usage_files:
            lines.append(f"- {labels['usage']}: " + ", ".join(f"`{item}`" for item in change.usage_files))
        if change.suggested_tests:
            lines.append(f"- {labels['tests']}: " + ", ".join(f"`{item}`" for item in change.suggested_tests))
        lines.append("")
    return "\n".join(lines)


def to_junit(result: AnalysisResult, language: str = "zh") -> str:
    language = normalize_language(language)
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
            reasons = [translate_reason(item, language) for item in change.reasons]
            lines.append(f'    <failure message="{escape(message)}">{escape("; ".join(reasons))}</failure>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def render(result: AnalysisResult, fmt: str, language: str = "zh") -> str:
    if fmt == "json":
        return to_json(result, language)
    if fmt == "junit":
        return to_junit(result, language)
    if fmt == "markdown":
        return to_markdown(result, language)
    raise ValueError(f"unsupported format: {fmt}")
