# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MCP server (stdio), built on the MCP Python SDK v2 `MCPServer` class."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from mcp.server import MCPServer

from siliconsmith._version import __version__
from siliconsmith.context import ServerContext
from siliconsmith.runners.base import Runner
from siliconsmith.runners.docker import DockerRunner, image_name
from siliconsmith.tools import register_all

logger = logging.getLogger("siliconsmith")

FRONTENDS = ("amaranth", "myhdl", "verilog")

INSTRUCTIONS = (
    "Siliconsmith designs small digital chips for Tiny Tapeout with open-source tools. "
    "Loop: create_project once, then write_spec first (spec.yaml: clock, pins, "
    "requirements R1, R2, ...), then tests with write_test (each test docstring says "
    "'Covers: R1'), then the design with write_rtl, then emit_verilog to generate and lint "
    "the Verilog, then run_tests on icarus and verilator. If a test fails, rerun it with "
    "waves=True and read signals with inspect_waves. Call status to see what has passed. "
    "Only the owner can approve the spec, on the command line: never try. "
    "Use only the tools this server lists. "
    "Every project tool takes the project name first. Write whole files, never diffs. "
    "Your design is the core module named after the project, using the Tiny Tapeout pins: "
    "clk, rst_n (active low), ena, ui_in, uio_in as inputs; uo_out, uio_out, uio_oe as outputs. "
    "The tt_um_<name> wrapper is generated; do not edit it. "
    "Rejected paths and bad arguments come back as tool errors that say what to do instead."
)


class RootError(ValueError):
    """The project root could not be chosen safely."""


def resolve_root(
    explicit: str | None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Pick the server root: --root, then SILICONSMITH_ROOT, CLAUDE_PROJECT_DIR, then cwd.

    "/" and the home directory are refused unless given with --root, because every project
    directory is created under the root.
    """
    env = os.environ if env is None else env
    if explicit is not None:
        if not explicit.strip():
            raise RootError("--root was given an empty value")
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_dir():
            raise RootError(f"--root {candidate} is not a directory")
        return candidate
    source = "the working directory"
    candidate = (cwd or Path.cwd()).resolve()
    for key in ("SILICONSMITH_ROOT", "CLAUDE_PROJECT_DIR"):
        value = env.get(key)
        if value:
            source = key
            candidate = Path(value).expanduser().resolve()
            break
    if candidate == Path("/") or candidate == Path.home().resolve():
        raise RootError(
            f"refusing to use {candidate} as the root; pass it explicitly with --root "
            "or start the server in a project directory"
        )
    if not candidate.is_dir():
        raise RootError(f"{source} {candidate} is not a directory")
    return candidate


def build_server(
    root: Path,
    default_frontend: str | None,
    *,
    runner: Runner | None = None,
    image: str | None = None,
) -> MCPServer:
    """Build the server. Tests pass a fake `runner`; the real server runs Docker containers."""
    image = image or image_name()
    runner = runner or DockerRunner(image)
    server = MCPServer("siliconsmith", instructions=INSTRUCTIONS, version=__version__)
    ctx = ServerContext(root=root, default_frontend=default_frontend, image=image, runner=runner)
    register_all(server, ctx)
    return server


def main(argv: Sequence[str] | None = None, prog: str = "siliconsmith server") -> int:
    parser = argparse.ArgumentParser(prog=prog, description="Siliconsmith MCP server")
    parser.add_argument("--root", help="directory that holds project directories")
    parser.add_argument("--default-frontend", choices=FRONTENDS, help="default HDL front end")
    args = parser.parse_args(argv)
    # Logs go to stderr: stdout carries the MCP stdio transport.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        root = resolve_root(args.root)
    except RootError as exc:
        parser.error(str(exc))
    logger.info("siliconsmith %s using root %s", __version__, root)
    build_server(root, args.default_frontend).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
