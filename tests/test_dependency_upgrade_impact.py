import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dependency_upgrade_impact.analyzer import analyze, compare_records, should_fail, summarize
from dependency_upgrade_impact.diff_input import parse_unified_diff
from dependency_upgrade_impact.models import DependencyChange, DependencyRecord, normalize_name
from dependency_upgrade_impact.parsers import (
    clean_specifier,
    dedupe_records,
    parse_package_json,
    parse_package_lock,
    parse_pyproject,
    parse_requirements,
    parse_simple_toml,
    spec_to_version,
)
from dependency_upgrade_impact.reporters import render, to_junit, to_json, to_markdown
from dependency_upgrade_impact.risk import is_risky_license, risk_level, score_change
from dependency_upgrade_impact.scanner import (
    import_matches,
    js_imports,
    npm_package_name,
    python_imports,
    scan_usage,
    suggest_tests,
)
from dependency_upgrade_impact.semver import classify_change, parse_version

class TempProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path


class SemverTests(unittest.TestCase):
    def test_parse_version_full(self):
        version = parse_version("1.2.3")
        self.assertEqual((version.major, version.minor, version.patch), (1, 2, 3))

    def test_parse_version_prefix(self):
        self.assertEqual(parse_version("^2.0.1").major, 2)

    def test_parse_version_missing_patch(self):
        self.assertEqual(parse_version("1.5").patch, 0)

    def test_parse_version_file_unknown(self):
        self.assertIsNone(parse_version("file:../pkg"))

    def test_classify_major(self):
        self.assertEqual(classify_change("1.9.9", "2.0.0"), "major")

    def test_classify_minor(self):
        self.assertEqual(classify_change("1.1.0", "1.2.0"), "minor")

    def test_classify_patch(self):
        self.assertEqual(classify_change("1.1.0", "1.1.1"), "patch")

    def test_classify_same(self):
        self.assertEqual(classify_change("1.1.1", "1.1.1"), "same")

    def test_classify_downgrade(self):
        self.assertEqual(classify_change("2.0.0", "1.9.9"), "downgrade")

    def test_classify_unknown(self):
        self.assertEqual(classify_change("git+abc", "1.0.0"), "unknown")


class ParserTests(TempProject):
    def test_parse_requirements_pinned(self):
        path = self.write("requirements.txt", "requests==2.32.3\n")
        records = parse_requirements(path)
        self.assertEqual(records[0].name, "requests")
        self.assertEqual(records[0].version, "2.32.3")

    def test_parse_requirements_comments(self):
        path = self.write("requirements.txt", "# c\n\nurllib3>=2.0 # note\n")
        self.assertEqual(parse_requirements(path)[0].version, "2.0")

    def test_parse_requirements_extras(self):
        path = self.write("requirements.txt", "requests[socks]==2.31.0\n")
        self.assertEqual(parse_requirements(path)[0].name, "requests")

    def test_parse_requirements_skips_options(self):
        path = self.write("requirements.txt", "-r base.txt\n--index-url https://packages.local/simple\n")
        self.assertEqual(parse_requirements(path), [])

    def test_parse_package_json_dependencies(self):
        path = self.write("package.json", '{"dependencies":{"react":"^18.2.0"}}')
        self.assertEqual(parse_package_json(path)[0].version, "18.2.0")

    def test_parse_package_json_dev_scope(self):
        path = self.write("package.json", '{"devDependencies":{"typescript":"~5.4.5"}}')
        self.assertEqual(parse_package_json(path)[0].scope, "dev")

    def test_parse_package_json_optional_scope(self):
        path = self.write("package.json", '{"optionalDependencies":{"fsevents":"2.3.3"}}')
        self.assertEqual(parse_package_json(path)[0].scope, "optional")

    def test_parse_package_json_root_engines(self):
        path = self.write("package.json", '{"engines":{"node":">=20"},"dependencies":{"x":"1.0.0"}}')
        self.assertEqual(parse_package_json(path)[0].engines["node"], ">=20")

    def test_parse_package_json_scripts(self):
        path = self.write("package.json", '{"scripts":{"prepare":"x"},"dependencies":{"x":"1.0.0"}}')
        self.assertIn("prepare", parse_package_json(path)[0].scripts)

    def test_parse_package_lock_v3_direct(self):
        path = self.write(
            "package-lock.json",
            '{"packages":{"":{"dependencies":{"react":"18"}},"node_modules/react":{"version":"18.2.0"}}}',
        )
        self.assertTrue(parse_package_lock(path)[0].direct)

    def test_parse_package_lock_v3_license(self):
        path = self.write(
            "package-lock.json",
            '{"packages":{"node_modules/x":{"version":"1.0.0","license":"MIT"}}}',
        )
        self.assertEqual(parse_package_lock(path)[0].license, "MIT")

    def test_parse_package_lock_v3_dev(self):
        path = self.write(
            "package-lock.json",
            '{"packages":{"node_modules/ts":{"version":"5.0.0","dev":true}}}',
        )
        self.assertEqual(parse_package_lock(path)[0].scope, "dev")

    def test_parse_package_lock_v2_requires(self):
        path = self.write(
            "package-lock.json",
            '{"dependencies":{"a":{"version":"1.0.0","requires":{"b":"1"}}}}',
        )
        records = parse_package_lock(path)
        self.assertEqual(len(records), 2)
        self.assertFalse(records[1].direct)

    def test_parse_pyproject_project_dependencies(self):
        path = self.write("pyproject.toml", '[project]\ndependencies = ["fastapi==0.111.0"]\n')
        self.assertEqual(parse_pyproject(path)[0].name, "fastapi")

    def test_parse_pyproject_optional_dependencies(self):
        path = self.write("pyproject.toml", '[project.optional-dependencies]\ndev = ["pytest==8.2.0"]\n')
        record = parse_pyproject(path)[0]
        self.assertEqual(record.scope, "optional:dev")

    def test_parse_pyproject_poetry_dependencies(self):
        path = self.write("pyproject.toml", '[tool.poetry.dependencies]\npython = "^3.9"\nrequests = "^2.31.0"\n')
        self.assertEqual(parse_pyproject(path)[0].name, "requests")

    def test_parse_pyproject_poetry_group(self):
        path = self.write("pyproject.toml", '[tool.poetry.group.dev.dependencies]\npytest = "^8.0.0"\n')
        self.assertEqual(parse_pyproject(path)[0].scope, "dev:dev")

    def test_parse_simple_toml_array(self):
        data = parse_simple_toml('[project]\ndependencies = [\n"a==1.0.0",\n"b==2.0.0",\n]\n')
        self.assertEqual(data["project"]["dependencies"], ["a==1.0.0", "b==2.0.0"])

    def test_parse_simple_toml_inline_table(self):
        data = parse_simple_toml('[x]\ny = {version = "^1.0.0"}\n')
        self.assertEqual(data["x"]["y"]["version"], "^1.0.0")

    def test_spec_to_version_equal(self):
        self.assertEqual(spec_to_version("==1.2.3"), "1.2.3")

    def test_spec_to_version_caret(self):
        self.assertEqual(spec_to_version("^1.2.3"), "1.2.3")

    def test_spec_to_version_range_first(self):
        self.assertEqual(spec_to_version(">=1.2,<2"), "1.2")

    def test_clean_specifier_fallback(self):
        self.assertEqual(clean_specifier("workspace:*"), "workspace:*")

    def test_dedupe_prefers_version(self):
        records = [DependencyRecord("x"), DependencyRecord("x", version="1.0.0")]
        self.assertEqual(dedupe_records(records)[0].version, "1.0.0")

    def test_dedupe_prefers_direct(self):
        records = [DependencyRecord("x", direct=False), DependencyRecord("x", direct=True)]
        self.assertTrue(dedupe_records(records)[0].direct)


class ScannerTests(TempProject):
    def test_python_imports_import(self):
        path = self.write("app.py", "import requests\n")
        self.assertIn("requests", python_imports(path))

    def test_python_imports_from(self):
        path = self.write("app.py", "from fastapi import FastAPI\n")
        self.assertIn("fastapi", python_imports(path))

    def test_python_imports_syntax_error(self):
        path = self.write("bad.py", "def nope(:\n")
        self.assertEqual(python_imports(path), set())

    def test_js_imports_import(self):
        path = self.write("ui.ts", 'import React from "react";\n')
        self.assertIn("react", js_imports(path))

    def test_js_imports_require(self):
        path = self.write("ui.js", 'const z = require("zod");\n')
        self.assertIn("zod", js_imports(path))

    def test_js_imports_ignores_relative(self):
        path = self.write("ui.js", 'import x from "./x";\n')
        self.assertEqual(js_imports(path), set())

    def test_npm_package_name_scoped(self):
        self.assertEqual(npm_package_name("@scope/pkg/sub"), "@scope/pkg")

    def test_npm_package_name_plain(self):
        self.assertEqual(npm_package_name("react/jsx-runtime"), "react")

    def test_import_matches_python_dash(self):
        self.assertTrue(import_matches("python", "my-lib", {"my_lib"}))

    def test_import_matches_npm_scope(self):
        self.assertTrue(import_matches("npm", "@scope/pkg", {"@scope/pkg"}))

    def test_scan_usage_finds_python(self):
        self.write("app.py", "import requests\n")
        change = DependencyChange("requests", "python", "updated")
        self.assertEqual(scan_usage(self.root, [change])[change.key], ["app.py"])

    def test_scan_usage_finds_js(self):
        self.write("ui.ts", 'import React from "react";\n')
        change = DependencyChange("react", "npm", "updated")
        self.assertEqual(scan_usage(self.root, [change])[change.key], ["ui.ts"])

    def test_scan_usage_skips_node_modules(self):
        self.write("node_modules/pkg/index.js", 'import React from "react";\n')
        change = DependencyChange("react", "npm", "updated")
        self.assertEqual(scan_usage(self.root, [change]), {})

    def test_suggest_tests_by_name(self):
        self.write("src/client.py", "import requests\n")
        self.write("tests/test_client.py", "def test_x(): pass\n")
        self.assertIn("tests/test_client.py", suggest_tests(self.root, ["src/client.py"]))

    def test_suggest_tests_all_when_no_usage(self):
        self.write("tests/test_smoke.py", "def test_x(): pass\n")
        self.assertIn("tests/test_smoke.py", suggest_tests(self.root, []))


class RiskAndAnalyzerTests(TempProject):
    def test_risk_level_high(self):
        self.assertEqual(risk_level(70), "high")

    def test_risk_level_medium(self):
        self.assertEqual(risk_level(40), "medium")

    def test_risk_level_low(self):
        self.assertEqual(risk_level(39), "low")

    def test_risky_license_gpl(self):
        self.assertTrue(is_risky_license("GPL-3.0"))

    def test_risky_license_mit(self):
        self.assertFalse(is_risky_license("MIT"))

    def test_score_major_used_high(self):
        before = DependencyRecord("react", "17.0.0", ecosystem="npm")
        after = DependencyRecord("react", "18.0.0", ecosystem="npm")
        score, reasons, _, _ = score_change("updated", "major", before, after, ["ui.ts"])
        self.assertGreaterEqual(score, 70)
        self.assertTrue(any("major" in item for item in reasons))

    def test_score_script_hint(self):
        after = DependencyRecord("x", "1.0.0", scripts={"postinstall": "node x"})
        _, _, _, scripts = score_change("added", "unknown", None, after, [])
        self.assertEqual(scripts, ["postinstall: node x"])

    def test_score_engine_hint(self):
        after = DependencyRecord("x", "1.0.0", engines={"node": ">=20"})
        _, _, engines, _ = score_change("added", "unknown", None, after, [])
        self.assertEqual(engines, ["node >=20"])

    def test_compare_updated(self):
        changes = compare_records([DependencyRecord("x", "1.0.0")], [DependencyRecord("x", "2.0.0")])
        self.assertEqual(changes[0].semver, "major")

    def test_compare_added(self):
        changes = compare_records([], [DependencyRecord("x", "1.0.0")])
        self.assertEqual(changes[0].change_type, "added")

    def test_compare_removed(self):
        changes = compare_records([DependencyRecord("x", "1.0.0")], [])
        self.assertEqual(changes[0].change_type, "removed")

    def test_compare_same_ignored(self):
        changes = compare_records([DependencyRecord("x", "1.0.0")], [DependencyRecord("x", "1.0.0")])
        self.assertEqual(changes, [])

    def test_summarize_counts(self):
        changes = [
            DependencyChange("x", "npm", "updated", semver="major", risk_score=80, risk_level="high"),
            DependencyChange("y", "npm", "added", risk_score=30, risk_level="low"),
        ]
        summary = summarize(changes)
        self.assertEqual(summary.total_changes, 2)
        self.assertEqual(summary.major, 1)

    def test_should_fail_none(self):
        change = DependencyChange("x", "npm", "updated", risk_score=100, risk_level="high")
        self.assertFalse(should_fail([change], "none"))

    def test_should_fail_min_score(self):
        change = DependencyChange("x", "npm", "updated", risk_score=50, risk_level="medium")
        self.assertTrue(should_fail([change], "high", min_score=50))

    def test_analyze_end_to_end(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        (before / "requirements.txt").write_text("requests==2.30.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("requests==3.0.0\n", encoding="utf-8")
        self.write("src/app.py", "import requests\n")
        result = analyze(str(before), str(after), source_root=str(self.root / "src"))
        self.assertEqual(result.summary.major, 1)
        self.assertEqual(result.changes[0].usage_files, ["app.py"])

    def test_analyze_no_changes(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        (before / "requirements.txt").write_text("x==1.0.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("x==1.0.0\n", encoding="utf-8")
        self.assertEqual(analyze(str(before), str(after)).summary.total_changes, 0)


class ReporterAndCliTests(TempProject):
    def make_result(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        (before / "requirements.txt").write_text("requests==2.30.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("requests==3.0.0\n", encoding="utf-8")
        return analyze(str(before), str(after), fail_on="none")

    def test_json_report_valid(self):
        data = json.loads(to_json(self.make_result()))
        self.assertEqual(data["summary"]["total_changes"], 1)

    def test_markdown_report_contains_name(self):
        self.assertIn("requests", to_markdown(self.make_result()))

    def test_junit_report_xmlish(self):
        self.assertIn("<testsuite", to_junit(self.make_result()))

    def test_render_rejects_bad_format(self):
        with self.assertRaises(ValueError):
            render(self.make_result(), "yaml")

    def test_diff_parse_before_after(self):
        before, after = parse_unified_diff(
            "diff --git a/requirements.txt b/requirements.txt\n"
            "--- a/requirements.txt\n+++ b/requirements.txt\n"
            "@@ -1 +1 @@\n-requests==2.0.0\n+requests==3.0.0\n"
        )
        self.assertEqual(before["requirements.txt"], ["requests==2.0.0"])
        self.assertEqual(after["requirements.txt"], ["requests==3.0.0"])

    def test_cli_json_stdout(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        (before / "requirements.txt").write_text("requests==2.30.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "dependency_upgrade_impact",
                "--before",
                str(before),
                "--after",
                str(after),
                "--format",
                "json",
                "--fail-on",
                "none",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["summary"]["minor"], 1)

    def test_cli_output_file(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        output = self.root / "report.md"
        (before / "requirements.txt").write_text("x==1.0.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("x==2.0.0\n", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "dependency_upgrade_impact",
                "--before",
                str(before),
                "--after",
                str(after),
                "--output",
                str(output),
                "--fail-on",
                "none",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Dependency Upgrade", output.read_text(encoding="utf-8"))

    def test_cli_gate_exit_2(self):
        before = self.root / "before"
        after = self.root / "after"
        before.mkdir()
        after.mkdir()
        (before / "requirements.txt").write_text("x==1.0.0\n", encoding="utf-8")
        (after / "requirements.txt").write_text("x==2.0.0\n", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "dependency_upgrade_impact",
                "--before",
                str(before),
                "--after",
                str(after),
                "--fail-on",
                "medium",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(proc.returncode, 2)

    def test_cli_diff(self):
        diff = self.write(
            "change.patch",
            """
            diff --git a/requirements.txt b/requirements.txt
            --- a/requirements.txt
            +++ b/requirements.txt
            @@ -1 +1 @@
            -x==1.0.0
            +x==1.1.0
            """,
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "dependency_upgrade_impact",
                "--diff",
                str(diff),
                "--format",
                "json",
                "--fail-on",
                "none",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["summary"]["minor"], 1)


class UtilityTests(unittest.TestCase):
    def test_normalize_name_lower(self):
        self.assertEqual(normalize_name("Requests"), "requests")

    def test_normalize_name_underscore(self):
        self.assertEqual(normalize_name("my_lib"), "my-lib")

    def test_record_key(self):
        self.assertEqual(DependencyRecord("X", ecosystem="python").key, "python:x")

    def test_change_key(self):
        self.assertEqual(DependencyChange("X", "npm", "added").key, "npm:x")

    def test_examples_exist(self):
        self.assertTrue((ROOT / "examples" / "before" / "package.json").exists())

    def test_readme_exists(self):
        self.assertTrue((ROOT / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
