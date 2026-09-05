#!/usr/bin/env python3
"""Run the Hub durable refresh CLI from the repository checkout."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hub.connection_manager_cli import main

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
