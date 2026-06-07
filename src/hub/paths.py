"""Shared paths for future hub services."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
GOVERNANCE_DIR = PROJECT_ROOT / "governance"
DOCS_DIR = PROJECT_ROOT / "docs"
