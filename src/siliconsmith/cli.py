# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Command line entry point: `siliconsmith`.

`server` starts the MCP server; later milestones add `status` and `approve`. Each one gets its
own `add_parser` call in `build_parser` and a branch in `main`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from siliconsmith._version import __version__


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
    # Later PRs add `status` and `approve` here.
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["server"]:
        # Imported here so `siliconsmith --version` does not load the MCP SDK.
        from siliconsmith.server import main as server_main

        return server_main(args_list[1:], prog="siliconsmith server")
    parser = build_parser()
    parser.parse_args(args_list)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
