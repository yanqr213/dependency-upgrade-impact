"""Risk scoring rules."""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from .models import DependencyChange, DependencyRecord


RISKY_LICENSE_MARKERS = ("gpl", "agpl", "lgpl", "sspl", "proprietary", "unknown")
RISKY_SCRIPT_NAMES = ("install", "postinstall", "preinstall", "prepare")


def score_change(
    change_type: str,
    semver: str,
    before: Optional[DependencyRecord],
    after: Optional[DependencyRecord],
    usage_files: Iterable[str],
) -> Tuple[int, List[str], List[str], List[str]]:
    score = 0
    reasons: List[str] = []
    engine_hints: List[str] = []
    script_hints: List[str] = []
    usage = list(usage_files)

    if change_type == "added":
        score += 20
        reasons.append("新增依赖需要确认供应链与许可")
    elif change_type == "removed":
        score += 35
        reasons.append("移除依赖可能导致运行时缺失")
    elif change_type == "updated":
        score += 15
        reasons.append("依赖版本发生更新")

    if semver == "major":
        score += 45
        reasons.append("major 升级通常包含破坏性变更")
    elif semver == "minor":
        score += 25
        reasons.append("minor 升级可能引入新行为")
    elif semver == "patch":
        score += 10
        reasons.append("patch 升级风险较低但仍需验证")
    elif semver == "downgrade":
        score += 35
        reasons.append("版本降级可能回退安全或兼容性修复")
    elif semver == "unknown" and change_type == "updated":
        score += 25
        reasons.append("版本格式无法判断 semver 风险")

    if after and not after.direct:
        score += 10
        reasons.append("间接依赖变化可能来自上游依赖树")
    if before and not before.direct:
        score += 10

    if usage:
        score += min(30, 10 + len(usage) * 3)
        reasons.append("源码中发现 import/require 使用")
    else:
        score += 5
        reasons.append("未发现源码直接使用，仍可能通过插件或动态加载使用")

    before_license = before.license if before else None
    after_license = after.license if after else None
    if before_license != after_license and after_license:
        score += 15
        reasons.append("许可信息发生变化")
    if after_license and is_risky_license(after_license):
        score += 25
        reasons.append(f"许可可能需要人工复核: {after_license}")

    engines = after.engines if after else {}
    for engine, spec in engines.items():
        engine_hints.append(f"{engine} {spec}")
    if engine_hints:
        score += 10
        reasons.append("依赖声明了运行时 engine 约束")

    scripts = after.scripts if after else {}
    for name in sorted(scripts):
        if name in RISKY_SCRIPT_NAMES:
            script_hints.append(f"{name}: {scripts[name]}")
    if script_hints:
        score += 20
        reasons.append("依赖包含安装期脚本")

    return min(score, 100), dedupe(reasons), engine_hints, script_hints


def risk_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def is_risky_license(value: str) -> bool:
    text = value.lower()
    return any(marker in text for marker in RISKY_LICENSE_MARKERS)


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
