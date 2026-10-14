#!/usr/bin/env python3
"""Finite Hub source discovery and durable sync entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
