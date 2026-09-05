#!/usr/bin/env python3
"""Hub design entry point; avoids the root hub.py package shadow."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.design_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
