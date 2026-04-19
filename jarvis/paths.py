"""Filesystem anchors for the project (repo root, package dir)."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
