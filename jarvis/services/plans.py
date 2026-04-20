"""Markdown plan management — create, list, read, save, delete .md files."""

from __future__ import annotations

import re
from datetime import datetime
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


def save_plan_to(path: Path, content: str) -> Path:
    """Save markdown content to an explicit path (ensures plans dir exists)."""
    _ensure_dir()
    p = path.expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def versioned_plan_path(name: str) -> Path:
    """
    Pick the next available versioned filename:
    - plans/<slug>.md (base)
    - plans/<slug>-v2.md
    - plans/<slug>-v3.md
    """
    base = plan_path(name)
    if not base.exists():
        return base
    stem = base.stem
    d = _ensure_dir()
    for n in range(2, 1000):
        cand = d / f"{stem}-v{n}.md"
        if not cand.exists():
            return cand
    return d / f"{stem}-v999.md"


def wrap_markdown(body: str, *, date_str: str | None = None) -> str:
    """
    Add lightweight YAML frontmatter so saved notes are sortable/searchable.
    Kept intentionally minimal (no heavy template enforcement).
    """
    now = datetime.now().isoformat(timespec="seconds")
    ds = (date_str or "").strip()
    fm_lines = ["---", f"created: {now}"]
    if ds:
        fm_lines.append(f"date: {ds}")
    fm_lines.append("---")
    b = (body or "").strip()
    return "\n".join(fm_lines) + "\n\n" + b + "\n"


def build_plan_content(topic: str, raw_markdown: str, *, date_str: str | None = None) -> str:
    """
    Canonicalize a generated plan before saving.
    - Ensures the document starts with a title
    - Wraps with frontmatter
    """
    t = (topic or "").strip()
    content = (raw_markdown or "").strip()
    if not content:
        content = f"# {t or 'Project plan'}\n\n## Tasks\n- [ ] Define the goal\n"
    if not content.lstrip().startswith("#"):
        title = t or "Project plan"
        content = f"# {title}\n\n{content}"
    return wrap_markdown(content, date_str=date_str)


def delete_plan(name: str) -> bool:
    p = plan_path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def plans_dir() -> Path:
    return _ensure_dir()
