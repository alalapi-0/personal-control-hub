#!/usr/bin/env python3
"""Versioned Hub connections entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.connection_cli import main as connection_main


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "metrics":
        from hub.metric_cli import main as metric_main
        return metric_main(sys.argv[2:])
    return connection_main()

if __name__ == "__main__":
    raise SystemExit(main())
