"""CLI entrypoint for personal-control-hub."""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="personal-control-hub CLI")
    subparsers = parser.add_subparsers(dest="command")

    mcp_parser = subparsers.add_parser("mcp", help="Read-only MCP registry and policy")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("list", help="List registered MCP capabilities")
    mcp_sub.add_parser("policy", help="List MCP approval levels L0-L3")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "mcp":
        from hub.services.mcp_registry_service import print_mcp_list, print_mcp_policy

        if args.mcp_command == "list":
            return print_mcp_list()
        if args.mcp_command == "policy":
            return print_mcp_policy()
        parser.error(f"unknown mcp subcommand: {args.mcp_command}")

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
