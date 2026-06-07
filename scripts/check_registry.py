#!/usr/bin/env python3
"""Validate external project registry schema and policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hub.services.project_registry_service import validate_registry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="external project registry check")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    result = validate_registry()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1

    print("=== External Project Registry Check ===")
    print(f"项目数：{result['project_count']}")
    print(f"已启用：{result['enabled_count']}")
    print(f"结果：{'ok' if result['valid'] else 'fail'}")
    print(f"硬阻塞：{len(result['hard_blockers'])}")
    print(f"警告：{len(result['warnings'])}")

    if result["hard_blockers"]:
        print("\n硬阻塞：")
        for item in result["hard_blockers"]:
            print(f"  - {item}")
    if result["warnings"]:
        print("\n警告：")
        for item in result["warnings"]:
            print(f"  - {item}")

    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
