"""Command-line entry point. Keep this thin: parse arguments, call the core."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from session_doc import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-doc",
        description="Turn coding-agent session logs into searchable HTML docs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("generate", help="Generate HTML docs from session logs.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        print("session-doc: 'generate' is not implemented yet.", file=sys.stderr)
        return 1
    parser.print_help()
    return 0
