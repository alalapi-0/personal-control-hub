#!/usr/bin/env python3
"""Hub connection entry point; keeps the legacy root hub.py from shadowing the package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.connection_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
