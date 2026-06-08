"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analyzer import analyze
from .diff_input import cleanup_materialized, materialize_diff
from .parsers import ParseError
from .reporters import render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dependency-upgrade-impact",
        description="离线分析依赖升级影响，输出 Markdown/JSON/JUnit 报告并支持 CI gate。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--before", help="升级前目录或单个依赖文件")
    input_group.add_argument("--diff", help="包含支持文件的 unified diff")
    parser.add_argument("--after", help="升级后目录或单个依赖文件；使用 --before 时必填")
    parser.add_argument("--source-root", help="源码目录，用于扫描 import/require 与映射测试")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "junit"),
        default="markdown",
        help="报告格式，默认 markdown",
    )
    parser.add_argument("--output", help="写入报告文件；默认输出到 stdout")
    parser.add_argument(
        "--fail-on",
        choices=("none", "low", "medium", "high"),
        default="high",
        help="达到指定风险等级时返回退出码 2，默认 high",
    )
    parser.add_argument("--min-score", type=int, help="达到指定风险分时返回退出码 2")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    materialized = []
    try:
        before = args.before
        after = args.after
        if args.diff:
            before, after = materialize_diff(args.diff)
            materialized = [before, after]
        elif not after:
            parser.error("--after is required when --before is used")

        result = analyze(
            before_path=before,
            after_path=after,
            source_root=args.source_root,
            fail_on=args.fail_on,
            min_score=args.min_score,
        )
        output = render(result, args.format)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 2 if result.summary.gate_failed else 0
    except ParseError as exc:
        sys.stderr.write(f"parse error: {exc}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"io error: {exc}\n")
        return 1
    finally:
        cleanup_materialized(materialized)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
