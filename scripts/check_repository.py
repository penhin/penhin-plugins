"""Check repository metadata and inline local Markdown links; no dependencies."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "CONTRIBUTING.md", "LICENSE",
    "docs/compatibility.md", "docs/releases.md",
)
STATUSES = {"planned", "in-development", "released"}
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


def check(root: Path) -> list[str]:
    errors: list[str] = []
    documents = list(root.glob("*.md")) + list((root / "docs").rglob("*.md"))
    for name in REQUIRED:
        if not (root / name).is_file():
            errors.append(f"Missing required file: {name}")
    try:
        catalog = json.loads((root / "plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return errors + [f"Cannot read plugins.json: {error}"]
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        return errors + ["plugins.json requires schema_version 1"]
    entries = catalog.get("plugins")
    if not isinstance(entries, list) or not entries:
        return errors + ["plugins.json requires a nonempty plugins list"]

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("Each plugin entry must be an object")
            continue
        plugin_id = entry.get("id")
        if not isinstance(plugin_id, str) or not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", plugin_id):
            errors.append(f"Invalid plugin id: {plugin_id!r}")
            continue
        if plugin_id in seen:
            errors.append(f"Duplicate plugin id: {plugin_id}")
        seen.add(plugin_id)
        expected_path = f"plugins/{plugin_id}"
        if entry.get("path") != expected_path:
            errors.append(f"{plugin_id}: path must be {expected_path}")
        status = entry.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{plugin_id}: invalid status")
        issue = entry.get("upstream_issue", "")
        if not isinstance(issue, str) or not re.fullmatch(r"https://github\.com/penhin/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*", issue):
            errors.append(f"{plugin_id}: upstream_issue must reference a penhin repository issue")
        plugin_root = root / expected_path
        readme = plugin_root / "README.md"
        if not readme.is_file():
            errors.append(f"{plugin_id}: README.md is missing")
        else:
            documents.extend(plugin_root.rglob("*.md"))
        if isinstance(status, str) and status in {"in-development", "released"}:
            try:
                package = tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
                project = package["project"]
                if not project.get("name") or not project.get("version"):
                    errors.append(f"{plugin_id}: package name and version are required")
                reference = project.get("entry-points", {}).get("penhin.plugins", {}).get(plugin_id)
                if not isinstance(reference, str) or ":" not in reference:
                    errors.append(f"{plugin_id}: penhin.plugins factory entry point is required")
            except (OSError, ValueError, KeyError, TypeError):
                errors.append(f"{plugin_id}: readable Python package metadata is required")

    plugins_root = root / "plugins"
    if plugins_root.is_dir():
        for directory in plugins_root.iterdir():
            if directory.is_dir() and not directory.name.startswith(".") and directory.name not in seen:
                errors.append(f"Unregistered plugin directory: {directory.name}")

    for document in sorted(set(documents)):
        if not document.is_file():
            continue
        content = document.read_text(encoding="utf-8")
        for match in LINK.finditer(content):
            target = match.group(1).strip()
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = (document.parent / unquote(parsed.path)).resolve()
            if not path.is_relative_to(root.resolve()) or not path.exists():
                errors.append(f"{document.relative_to(root)}: invalid local link {target}")
    return errors


if __name__ == "__main__":
    problems = check(ROOT)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        raise SystemExit(1)
    print("Plugin catalog and local documentation links are valid.")
