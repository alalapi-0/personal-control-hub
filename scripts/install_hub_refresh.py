#!/usr/bin/env python3
"""Render the separate finite-source daily refresh service and timer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.systemd_refresh import main

if __name__ == "__main__":
    raise SystemExit(main())
