# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Command line entry point: `siliconsmith`.

`server` starts the MCP server. `status <dir>` prints the validation table and rewrites
`docs/verification.md`. `approve <dir>` is the owner's approval of the spec; it needs a terminal.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from siliconsmith._version import __version__

if TYPE_CHECKING:
    from siliconsmith.project import Project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="siliconsmith",
        description="Siliconsmith: open-source chip design toolchain for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    # `server` is dispatched in main() before parsing, so its own options stay in server.py.
    sub.add_parser(
        "server", help="run the MCP server over stdio (see `siliconsmith server --help`)"
    )
    for name, text in (
        ("status", "show which validation steps pass and write docs/verification.md"),
        ("approve", "the owner approves spec.yaml (needs a terminal; agents must not run this)"),
    ):
        command = sub.add_parser(name, help=text, description=text)
        command.add_argument("dir", help="the project folder (it contains siliconsmith.toml)")
        if name == "status":
            command.add_argument(
                "--image",
                help="toolchain image name:tag the server uses (default: SILICONSMITH_IMAGE or "
                "the image for this checkout); a different image makes the table stale",
            )
    return parser


def _load(directory: str) -> Project | None:
    """The project in `directory`, or None after printing why it is not one."""
    from mcp.server.mcpserver.exceptions import ToolError

    from siliconsmith.project import load_project

    path = Path(directory).expanduser().resolve()
    try:
        return load_project(path.parent, path.name)
    except ToolError as exc:
        print(f"siliconsmith: {directory} is not a Siliconsmith project: {exc}", file=sys.stderr)
        return None


def _status(directory: str, image: str | None) -> int:
    from mcp.server.mcpserver.exceptions import ToolError

    from siliconsmith.ledger import load_ledger
    from siliconsmith.runners.docker import image_name
    from siliconsmith.status import refresh_status

    project = _load(directory)
    if project is None:
        return 2
    try:
        image = image or image_name()
        report = refresh_status(project, image)
        runs = load_ledger(project.dir).ledger.runs
    except ToolError as exc:
        print(f"siliconsmith: {exc}", file=sys.stderr)
        return 1
    print("\n".join(report.summary_lines()))
    if runs and runs[-1].image != image:
        print(
            f"Note: the last run used image {runs[-1].image} but this table used {image}, so "
            "steps may show stale only because of the image. If the server runs with a different "
            "SILICONSMITH_IMAGE, pass it with --image."
        )
    return 0


def _approve(directory: str) -> int:
    from siliconsmith.approval import REFUSAL, approve_project, have_terminal

    # The terminal check comes first, so a pipe or an agent's subprocess learns nothing else.
    if not have_terminal(sys.stdin, sys.stdout):
        print(REFUSAL)
        return 1
    project = _load(directory)
    if project is None:
        return 2
    return approve_project(project, stdin=sys.stdin, stdout=sys.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["server"]:
        # Imported here so `siliconsmith --version` does not load the MCP SDK.
        from siliconsmith.server import main as server_main

        return server_main(args_list[1:], prog="siliconsmith server")
    parser = build_parser()
    args = parser.parse_args(args_list)
    if args.command == "status":
        return _status(args.dir, args.image)
    if args.command == "approve":
        return _approve(args.dir)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
