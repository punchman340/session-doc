"""Command-line entry point. Keep this thin: parse arguments, call the core."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from session_doc import __version__
from session_doc.config import ConfigError, default_config_path, load_config
from session_doc.generate import (
    GenerateError,
    GitWorkTreeError,
    default_out_dir,
    default_source_dir,
    generate,
)
from session_doc.hook import print_config_text, run_hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-doc",
        description="Turn coding-agent session logs into searchable HTML docs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    gen = subparsers.add_parser(
        "generate",
        help="Generate HTML docs from session logs.",
        description="Render new and changed session logs into static HTML docs. "
        "Unchanged sessions are skipped.",
    )
    gen.add_argument(
        "--source",
        type=Path,
        metavar="DIR",
        help="directory of session logs, searched recursively "
        "(default: Claude Code's projects dir, ~/.claude/projects)",
    )
    gen.add_argument(
        "--out", type=Path, metavar="DIR", help="output directory (default: ~/session-doc)"
    )
    gen.add_argument(
        "--no-archive",
        action="store_true",
        help="do not copy source logs into the output dir's raw/ dir",
    )
    gen.add_argument(
        "--allow-in-git",
        action="store_true",
        help="allow an output dir inside a git work tree",
    )
    hook = subparsers.add_parser(
        "hook",
        help="Update the docs for one session (Claude Code SessionEnd hook).",
        description="Read a SessionEnd hook input (JSON) from stdin and update the docs for "
        "that session only. Always exits 0 and prints nothing on success; errors are "
        "appended to <out>/hook.log.",
    )
    hook.add_argument(
        "--out", type=Path, metavar="DIR", help="output directory (default: ~/session-doc)"
    )
    hook.add_argument(
        "--allow-in-git",
        action="store_true",
        help="allow an output dir inside a git work tree",
    )
    hook.add_argument(
        "--print-config",
        action="store_true",
        help="print the settings.json snippet that registers this hook, then exit",
    )
    return parser


def _hook(args: argparse.Namespace) -> int:
    if args.print_config:
        sys.stdout.write(print_config_text())
        return 0
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    run_hook(
        raw,
        args.out if args.out is not None else default_out_dir(),
        config_path=default_config_path(),
        allow_in_git=args.allow_in_git,
    )
    return 0


def _generate(args: argparse.Namespace) -> int:
    try:
        config = load_config(default_config_path())
        result = generate(
            source=args.source if args.source is not None else default_source_dir(),
            out=args.out if args.out is not None else default_out_dir(),
            archive=not args.no_archive,
            allow_in_git=args.allow_in_git,
            decision_phrases=config.decision_phrases,
        )
    except GitWorkTreeError as exc:
        print(
            f"session-doc: error: {exc} Pass --allow-in-git to write there anyway.", file=sys.stderr
        )
        return 1
    except (ConfigError, GenerateError, OSError) as exc:
        print(f"session-doc: error: {exc}", file=sys.stderr)
        return 1
    counts = [f"{result.new} new", f"{result.changed} changed", f"{result.skipped} skipped"]
    if result.rerendered:
        counts.append(f"{result.rerendered} re-rendered")
    print(f"Sessions: {', '.join(counts)}")
    print(f"Output: {result.index}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate(args)
    if args.command == "hook":
        return _hook(args)
    parser.print_help()
    return 0
