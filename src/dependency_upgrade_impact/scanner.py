"""Source import scanning and test mapping."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Union

from .models import DependencyChange, normalize_name


JS_IMPORT_RE = re.compile(
    r"(?:import\s+(?:[^'\"]+\s+from\s+)?|export\s+[^'\"]+\s+from\s+|require\()\s*['\"]([^'\"]+)['\"]"
)

PY_EXTENSIONS = {".py"}
JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}


PathLike = Union[os.PathLike, str]


def scan_usage(source_root: PathLike, changes: Iterable[DependencyChange]) -> Dict[str, List[str]]:
    """Return dependency key -> source files that import it."""

    root = Path(source_root)
    if not root.exists() or not root.is_dir():
        return {}
    wanted = {change.key: change for change in changes}
    found: Dict[str, Set[str]] = {key: set() for key in wanted}
    for file_path in iter_source_files(root):
        imports = python_imports(file_path) if file_path.suffix == ".py" else js_imports(file_path)
        if not imports:
            continue
        rel = str(file_path.relative_to(root)).replace("\\", "/")
        for key, change in wanted.items():
            if import_matches(change.ecosystem, change.name, imports):
                found[key].add(rel)
    return {key: sorted(paths) for key, paths in found.items() if paths}


def iter_source_files(root: Path):
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            path = Path(current) / filename
            if path.suffix in PY_EXTENSIONS.union(JS_EXTENSIONS):
                yield path


def python_imports(path: Path) -> Set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    imports: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def js_imports(path: Path) -> Set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return set()
    imports: Set[str] = set()
    for match in JS_IMPORT_RE.finditer(text):
        spec = match.group(1)
        if spec.startswith("."):
            continue
        imports.add(npm_package_name(spec))
    return imports


def npm_package_name(specifier: str) -> str:
    if specifier.startswith("@"):
        parts = specifier.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else specifier
    return specifier.split("/", 1)[0]


def import_matches(ecosystem: str, dependency_name: str, imports: Set[str]) -> bool:
    normalized_dep = normalize_name(dependency_name)
    if ecosystem == "npm":
        return dependency_name in imports or normalized_dep in {normalize_name(item) for item in imports}
    candidates = {normalized_dep, normalized_dep.replace("-", "_")}
    normalized_imports = {normalize_name(item) for item in imports} | {item.replace("-", "_") for item in imports}
    return bool(candidates & normalized_imports)


def suggest_tests(source_root: PathLike, usage_files: Iterable[str]) -> List[str]:
    root = Path(source_root)
    if not root.exists():
        return []
    candidates: Set[str] = set()
    test_files = [path for path in iter_source_files(root) if is_test_file(path)]
    usage_parts = [Path(path).parts for path in usage_files]
    for test_file in test_files:
        rel = str(test_file.relative_to(root)).replace("\\", "/")
        if not usage_parts:
            candidates.add(rel)
            continue
        stem = test_file.stem.lower().replace("test_", "").replace("_test", "")
        for parts in usage_parts:
            if any(stem and stem in part.lower() for part in parts):
                candidates.add(rel)
            if parts and parts[0] in test_file.parts:
                candidates.add(rel)
    return sorted(candidates)[:20]


def is_test_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".spec.js")
        or name.endswith(".test.ts")
        or name.endswith(".spec.ts")
        or "tests" in {part.lower() for part in path.parts}
    )
