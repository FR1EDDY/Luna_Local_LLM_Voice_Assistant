"""Markdown plan management — create, list, read, save, delete .md files."""

from __future__ import annotations

import re
from pathlib import Path

from jarvis.paths import PROJECT_ROOT

PLANS_DIR = PROJECT_ROOT / "plans"


def _ensure_dir() -> Path:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    return PLANS_DIR


def _slug(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "-", name)
    return name[:60] or "plan"


def plan_path(name: str) -> Path:
    return _ensure_dir() / f"{_slug(name)}.md"


def list_plans() -> list[str]:
    """Return plan names (without .md) sorted alphabetically."""
    d = _ensure_dir()
    return sorted(p.stem for p in d.glob("*.md"))


def get_plan(name: str) -> str | None:
    p = plan_path(name)
    if p.exists():
        return p.read_text(encoding="utf-8")
    # fuzzy: try matching stem contains name
    d = _ensure_dir()
    slug = _slug(name)
    for f in d.glob("*.md"):
        if slug in f.stem or f.stem in slug:
            return f.read_text(encoding="utf-8")
    return None


def get_plan_by_path(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def save_plan(name: str, content: str) -> Path:
    p = plan_path(name)
    p.write_text(content, encoding="utf-8")
    return p


def delete_plan(name: str) -> bool:
    p = plan_path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def plans_dir() -> Path:
    return _ensure_dir()
