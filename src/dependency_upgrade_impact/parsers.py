"""Manifest and lock-file parsers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Union

from .models import DependencyRecord, normalize_name
from .semver import parse_version


_REQ_NAME_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)\s*([<>=!~].*)?$"
)


class ParseError(ValueError):
    pass


PathLike = Union[os.PathLike, str]


def parse_directory(path: PathLike) -> Tuple[List[DependencyRecord], List[str]]:
    """Parse supported dependency files below a directory."""

    base = Path(path)
    if not base.exists():
        raise ParseError(f"path does not exist: {base}")
    if base.is_file():
        return parse_file(base), [str(base)]

    records: List[DependencyRecord] = []
    sources: List[str] = []
    for filename in ("package-lock.json", "package.json", "requirements.txt", "pyproject.toml"):
        file_path = base / filename
        if file_path.exists():
            records.extend(parse_file(file_path))
            sources.append(str(file_path))
    return dedupe_records(records), sources


def parse_file(path: PathLike) -> List[DependencyRecord]:
    file_path = Path(path)
    try:
        if file_path.name == "package.json":
            return parse_package_json(file_path)
        if file_path.name == "package-lock.json":
            return parse_package_lock(file_path)
        if file_path.name == "requirements.txt":
            return parse_requirements(file_path)
        if file_path.name == "pyproject.toml":
            return parse_pyproject(file_path)
    except json.JSONDecodeError as exc:
        raise ParseError(f"cannot parse {file_path}: {exc}") from exc
    return []


def parse_package_json(path: PathLike) -> List[DependencyRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records: List[DependencyRecord] = []
    sections = {
        "dependencies": "runtime",
        "devDependencies": "dev",
        "optionalDependencies": "optional",
        "peerDependencies": "peer",
    }
    for section, scope in sections.items():
        for name, spec in data.get(section, {}).items():
            records.append(
                DependencyRecord(
                    name=name,
                    version=clean_specifier(str(spec)),
                    specifier=str(spec),
                    ecosystem="npm",
                    source=str(path),
                    scope=scope,
                    direct=True,
                )
            )
    root_scripts = data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {}
    root_engines = data.get("engines", {}) if isinstance(data.get("engines"), dict) else {}
    if root_scripts or root_engines:
        for idx, record in enumerate(records):
            records[idx] = DependencyRecord(
                **{**record.__dict__, "scripts": root_scripts, "engines": root_engines}
            )
    return records


def parse_package_lock(path: PathLike) -> List[DependencyRecord]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records: List[DependencyRecord] = []
    packages = data.get("packages")
    root_deps = set()
    if isinstance(packages, dict):
        root = packages.get("", {})
        if isinstance(root, dict):
            for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                root_deps.update((root.get(section) or {}).keys())
        for package_path, meta in packages.items():
            if not package_path or "node_modules/" not in package_path:
                continue
            name = package_path.split("node_modules/")[-1]
            records.append(
                DependencyRecord(
                    name=name,
                    version=str(meta.get("version", "")),
                    ecosystem="npm",
                    source=str(path),
                    scope="dev" if meta.get("dev") else "runtime",
                    direct=name in root_deps if root_deps else True,
                    license=meta.get("license"),
                    engines=meta.get("engines", {}) if isinstance(meta.get("engines"), dict) else {},
                    scripts=meta.get("scripts", {}) if isinstance(meta.get("scripts"), dict) else {},
                )
            )
        return records

    dependencies = data.get("dependencies", {})
    for name, meta in dependencies.items():
        records.append(
            DependencyRecord(
                name=name,
                version=str(meta.get("version", "")),
                ecosystem="npm",
                source=str(path),
                scope="dev" if meta.get("dev") else "runtime",
                direct=True,
                license=meta.get("license"),
                engines=meta.get("engines", {}) if isinstance(meta.get("engines"), dict) else {},
            )
        )
        for child in (meta.get("requires") or {}).keys():
            records.append(
                DependencyRecord(
                    name=child,
                    version="",
                    ecosystem="npm",
                    source=str(path),
                    direct=False,
                    parent=name,
                )
            )
    return records


def parse_requirements(path: PathLike) -> List[DependencyRecord]:
    records: List[DependencyRecord] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://", "git+")):
            continue
        match = _REQ_NAME_RE.match(line)
        if not match:
            continue
        name = match.group(1).split("[", 1)[0]
        spec = (match.group(2) or "").strip()
        version = spec_to_version(spec)
        records.append(
            DependencyRecord(
                name=name,
                version=version,
                specifier=spec,
                ecosystem="python",
                source=str(path),
                direct=True,
            )
        )
    return records


def parse_pyproject(path: PathLike) -> List[DependencyRecord]:
    data = parse_simple_toml(Path(path).read_text(encoding="utf-8"))
    records: List[DependencyRecord] = []
    project = data.get("project", {})
    for dep in project.get("dependencies", []) or []:
        records.append(_record_from_pep508(dep, path, "runtime"))
    optional = project.get("optional-dependencies", {}) or {}
    for extra, deps in optional.items():
        for dep in deps or []:
            records.append(_record_from_pep508(dep, path, f"optional:{extra}"))

    poetry = data.get("tool", {}).get("poetry", {})
    for section, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev")):
        for name, value in (poetry.get(section, {}) or {}).items():
            if normalize_name(name) == "python":
                continue
            spec = value if isinstance(value, str) else value.get("version", "")
            records.append(
                DependencyRecord(
                    name=name,
                    version=spec_to_version(str(spec)),
                    specifier=str(spec),
                    ecosystem="python",
                    source=str(path),
                    scope=scope,
                    direct=True,
                )
            )
    groups = poetry.get("group", {}) or {}
    for group_name, group_data in groups.items():
        deps = group_data.get("dependencies", {}) if isinstance(group_data, dict) else {}
        for name, value in deps.items():
            spec = value if isinstance(value, str) else value.get("version", "")
            records.append(
                DependencyRecord(
                    name=name,
                    version=spec_to_version(str(spec)),
                    specifier=str(spec),
                    ecosystem="python",
                    source=str(path),
                    scope=f"dev:{group_name}",
                    direct=True,
                )
            )
    return records


def parse_simple_toml(text: str) -> Dict[str, object]:
    """Parse the small TOML subset needed for dependency declarations.

    Python 3.9 has no tomllib. This parser intentionally handles only tables,
    strings, string arrays, and inline tables because those cover common
    pyproject dependency metadata used by this tool.
    """

    root: Dict[str, object] = {}
    current: Dict[str, object] = root
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = strip_toml_comment(raw).strip()
        index += 1
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table_name = line.strip("[]").strip()
            current = root
            for part in table_name.split("."):
                current = current.setdefault(part, {})  # type: ignore[assignment]
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().strip('"').strip("'")
        value = value.strip()
        while value.startswith("[") and not value.rstrip().endswith("]") and index < len(lines):
            value += "\n" + strip_toml_comment(lines[index]).strip()
            index += 1
        current[key] = parse_toml_value(value)
    return root


def strip_toml_comment(line: str) -> str:
    quote = None
    for idx, char in enumerate(line):
        if char in ("'", '"'):
            quote = char if quote is None else None if quote == char else quote
        elif char == "#" and quote is None:
            return line[:idx]
    return line


def parse_toml_value(value: str):
    value = value.strip().rstrip(",")
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_toml_value(item) for item in split_toml_list(inner)]
    if value.startswith("{") and value.endswith("}"):
        result: Dict[str, object] = {}
        inner = value[1:-1].strip()
        for item in split_toml_list(inner):
            if "=" in item:
                key, raw = item.split("=", 1)
                result[key.strip().strip('"').strip("'")] = parse_toml_value(raw)
        return result
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def split_toml_list(value: str) -> List[str]:
    items: List[str] = []
    quote = None
    depth = 0
    start = 0
    for idx, char in enumerate(value):
        if char in ("'", '"'):
            quote = char if quote is None else None if quote == char else quote
        elif quote is None:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                item = value[start:idx].strip()
                if item:
                    items.append(item)
                start = idx + 1
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return items


def dedupe_records(records: Iterable[DependencyRecord]) -> List[DependencyRecord]:
    by_key: Dict[str, DependencyRecord] = {}
    for record in records:
        key = record.key
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = record
            continue
        by_key[key] = prefer_record(previous, record)
    return sorted(by_key.values(), key=lambda item: (item.ecosystem, normalize_name(item.name)))


def prefer_record(old: DependencyRecord, new: DependencyRecord) -> DependencyRecord:
    if not old.version and new.version:
        return new
    if old.direct is False and new.direct is True:
        return new
    if not old.license and new.license:
        return new
    return old


def _record_from_pep508(value: str, path: PathLike, scope: str) -> DependencyRecord:
    name = re.split(r"\s*[<>=!~;\[]", value.strip(), maxsplit=1)[0].strip()
    spec = value[len(name) :].split(";", 1)[0].strip()
    return DependencyRecord(
        name=name,
        version=spec_to_version(spec),
        specifier=spec,
        ecosystem="python",
        source=str(path),
        scope=scope,
        direct=True,
    )


def spec_to_version(spec: str) -> str:
    if not spec:
        return ""
    for prefix in ("===", "==", "~=", ">=", "<=", ">", "<", "^", "~"):
        if spec.strip().startswith(prefix):
            candidate = spec.strip()[len(prefix) :].split(",", 1)[0].strip()
            return candidate
    if parse_version(spec):
        return spec.strip()
    return ""


def clean_specifier(value: str) -> str:
    return spec_to_version(value) or value.strip().lstrip("^~<>= ")
