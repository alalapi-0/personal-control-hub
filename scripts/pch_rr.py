#!/usr/bin/env python3
"""Run only from a protected PCH installation; disabled without local provisioning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'src'))
from hub.rr_cli import main

if __name__ == '__main__':
    raise SystemExit(main())
