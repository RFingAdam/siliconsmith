# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Host-side file access for project directories (PLAN.md sections 5.1 and 5.3).

Every read or write the server makes inside a project goes through this module. The rules:

* a path is relative, uses `/`, has no `..`, no NUL byte and no component that starts with a dot;
* every component is opened with `O_NOFOLLOW` through a directory file descriptor, so a symlink
  planted anywhere in the path (for example by code that ran in the container under `build/`)
  cannot lead the host outside the project;
* only regular files with exactly one hard link are read or replaced;
* reads have a size cap, and writes are atomic (temp file, fsync, rename);
* writers hold a per-project lock (`.siliconsmith/lock`).

`classify_write` is the per-front-end write allow-list used by the write tools.
"""

from __future__ import annotations

import errno
import fcntl
import keyword
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")

# IEEE 1800-2017 reserved words (a superset of the Verilog-2005 ones). The project name becomes
# a Verilog module name, so none of these can be used.
_VERILOG_KEYWORD_TEXT = """
    accept_on alias always always_comb always_ff always_latch and assert assign assume automatic
    before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex casez cell chandle
    checker class clocking cmos config const constraint context continue cover covergroup
    coverpoint cross deassign default defparam design disable dist do edge else end endcase
    endchecker endclass endclocking endconfig endfunction endgenerate endgroup endinterface
    endmodule endpackage endprimitive endprogram endproperty endsequence endspecify endtable
    endtask enum event eventually expect export extends extern final first_match for force
    foreach forever fork forkjoin function generate genvar global highz0 highz1 if iff ifnone
    ignore_bins illegal_bins implements implies import incdir include initial inout input inside
    instance int integer interconnect interface intersect join join_any join_none large let
    liblist library local localparam logic longint macromodule matches medium modport module
    nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or output
    package packed parameter pmos posedge primitive priority program property protected pull0
    pull1 pulldown pullup pulsestyle_ondetect pulsestyle_onevent pure rand randc randcase
    randsequence rcmos real realtime ref reg reject_on release repeat restrict return rnmos
    rpmos rtran rtranif0 rtranif1 s_always s_eventually s_nexttime s_until s_until_with scalared
    sequence shortint shortreal showcancelled signed small soft solve specify specparam static
    string strong strong0 strong1 struct super supply0 supply1 sync_accept_on sync_reject_on
    table tagged task this throughout time timeprecision timeunit tran tranif0 tranif1 tri tri0
    tri1 triand trior trireg type typedef union unique unique0 unsigned until until_with untyped
    use uwire var vectored virtual void wait wait_order wand weak weak0 weak1 while wildcard
    wire with within wor xnor xor
"""
VERILOG_KEYWORDS = frozenset(_VERILOG_KEYWORD_TEXT.split())

# Names that would clash with something else in a project: the testbench top, the wrapper file,
# or a module that `hdl/<name>.py` would shadow because /work/hdl is on sys.path.
RESERVED_NAMES = frozenset(
    {"tb", "project", "test", "amaranth", "myhdl", "cocotb", "cocotb_tools", "siliconsmith"}
)

Category = Literal["rtl", "spec", "test", "doc"]


def _err(message: str) -> ToolError:
    return ToolError(message)


def validate_project_name(name: str) -> str:
    """Return `name` if it is usable as a project and module name, else raise ToolError."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise _err(
            f"invalid project name {name!r}: use 1-31 characters, starting with a lowercase "
            "letter, then lowercase letters, digits or underscores (for example 'counter8')"
        )
    if name in VERILOG_KEYWORDS:
        raise _err(f"invalid project name {name!r}: it is a Verilog/SystemVerilog keyword")
    if keyword.iskeyword(name):
        raise _err(f"invalid project name {name!r}: it is a Python keyword")
    if name in sys.stdlib_module_names or name in RESERVED_NAMES:
        raise _err(
            f"invalid project name {name!r}: it clashes with a Python module or a file that "
            "every project already has; pick a more specific name"
        )
    return name


def validate_relpath(rel: str) -> tuple[str, ...]:
    """Check a project-relative path and return its components."""
    if not isinstance(rel, str) or not rel:
        raise _err("path is empty; give a project-relative path such as 'test/test.py'")
    if "\0" in rel:
        raise _err("path contains a NUL byte")
    if "\\" in rel:
        raise _err("path contains a backslash; use '/' as the separator")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise _err(f"path {rel!r} is absolute; give a path relative to the project directory")
    parts = tuple(rel.split("/"))
    for part in parts:
        if part in ("", "."):
            raise _err(f"path {rel!r} has an empty or '.' component")
        if part == "..":
            raise _err(f"path {rel!r} contains '..'")
        if part.startswith("."):
            raise _err(f"path {rel!r} has a component that starts with a dot ({part!r})")
    return parts


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dir(path: Path) -> int:
    try:
        return os.open(path, _DIR_FLAGS)
    except OSError as exc:
        raise _err(f"cannot open project directory {path.name!r}: {exc.strerror}") from exc


def _descend(fd: int, part: str, *, create: bool, shown: str) -> int:
    """Open sub-directory `part` of `fd` without following symlinks; the caller owns the result."""
    try:
        return os.open(part, _DIR_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise _err(f"{shown!r} does not exist") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _err(f"{shown!r} passes through a symlink or a non-directory") from exc
        raise _err(f"cannot open {shown!r}: {exc.strerror}") from exc
    try:
        os.mkdir(part, 0o755, dir_fd=fd)
        return os.open(part, _DIR_FLAGS, dir_fd=fd)
    except OSError as exc:
        raise _err(f"cannot create directory for {shown!r}: {exc.strerror}") from exc


@contextmanager
def _parent_dir(project_dir: Path, parts: tuple[str, ...], *, create: bool) -> Iterator[int]:
    """Yield a descriptor for the directory that holds the last component of `parts`."""
    opened = [_open_dir(project_dir)]
    try:
        for index, part in enumerate(parts[:-1]):
            shown = "/".join(parts[: index + 1])
            opened.append(_descend(opened[-1], part, create=create, shown=shown))
        yield opened[-1]
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _check_regular(st: os.stat_result, rel: str) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise _err(f"{rel!r} is not a regular file")
    if st.st_nlink != 1:
        raise _err(f"{rel!r} has more than one hard link; refusing to touch it")


def safe_read(project_dir: Path, rel: str, max_bytes: int) -> bytes:
    """Read a regular, single-link file inside `project_dir`, refusing files over `max_bytes`."""
    parts = validate_relpath(rel)
    with _parent_dir(project_dir, parts, create=False) as dfd:
        try:
            fd = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd
            )
        except FileNotFoundError:
            raise _err(f"{rel!r} does not exist") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _err(f"{rel!r} is a symlink; refusing to follow it") from exc
            raise _err(f"cannot open {rel!r}: {exc.strerror}") from exc
        try:
            st = os.fstat(fd)
            _check_regular(st, rel)
            if st.st_size > max_bytes:
                raise _err(f"{rel!r} is {st.st_size} bytes; the limit is {max_bytes} bytes")
            data = b""
            while len(data) <= max_bytes:
                chunk = os.read(fd, max_bytes + 1 - len(data))
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
    if len(data) > max_bytes:
        raise _err(f"{rel!r} is larger than the limit of {max_bytes} bytes")
    return data


def atomic_write(project_dir: Path, rel: str, data: bytes) -> None:
    """Write `data` to `rel` atomically, creating parent directories without following symlinks.

    Refuses if the existing target is a symlink, not a regular file, or has several hard links.
    """
    parts = validate_relpath(rel)
    name = parts[-1]
    with _parent_dir(project_dir, parts, create=True) as dfd:
        with suppress(FileNotFoundError):
            _check_regular(os.stat(name, dir_fd=dfd, follow_symlinks=False), rel)
        tmp = f".{name}.{os.getpid()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(tmp, flags, 0o644, dir_fd=dfd)
        except OSError as exc:
            raise _err(f"cannot write {rel!r}: {exc.strerror}") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.fsync(dfd)
        except OSError as exc:
            raise _err(f"cannot write {rel!r}: {exc.strerror}") from exc
        finally:
            with suppress(FileNotFoundError):
                os.unlink(tmp, dir_fd=dfd)


def walk_files(
    project_dir: Path, *, skip_dirs: frozenset[str] = frozenset()
) -> list[tuple[str, int]]:
    """List `(relative path, size)` for regular files, sorted, without following symlinks.

    Symlinks, special files and dot-files or dot-directories are skipped. `skip_dirs` names
    directories to leave out at any depth (for example `build`).
    """
    out: list[tuple[str, int]] = []

    def scan(fd: int, prefix: str) -> None:
        with os.scandir(fd) as entries:
            items = sorted(entries, key=lambda e: e.name)
        for entry in items:
            if entry.name.startswith("."):
                continue
            rel = prefix + entry.name
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name in skip_dirs:
                    continue
                try:
                    sub = os.open(entry.name, _DIR_FLAGS, dir_fd=fd)
                except OSError:
                    continue
                try:
                    scan(sub, rel + "/")
                finally:
                    os.close(sub)
            elif entry.is_file(follow_symlinks=False):
                out.append((rel, entry.stat(follow_symlinks=False).st_size))

    root_fd = _open_dir(project_dir)
    try:
        scan(root_fd, "")
    finally:
        os.close(root_fd)
    return out


@contextmanager
def project_lock(project_dir: Path) -> Iterator[None]:
    """Hold an exclusive lock on the project while writing (`.siliconsmith/lock`)."""
    with _parent_dir(project_dir, (".siliconsmith", "lock"), create=True) as dfd:
        try:
            fd = os.open(
                "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dfd
            )
        except OSError as exc:
            raise _err(f"cannot open the project lock: {exc.strerror}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the lock


def _never(rel: str, why: str) -> ToolError:
    return _err(f"{rel!r} cannot be written by a tool: {why}")


def classify_write(name: str, frontend: str, rel: str) -> Category:
    """Classify a write target (PLAN.md section 5.3) or raise ToolError naming the right file.

    Categories: `rtl` (the design source), `spec` (`spec.yaml`), `test` (cocotb tests under
    `test/`) and `doc` (Markdown under `docs/`).
    """
    parts = validate_relpath(rel)
    last = parts[-1]
    top = parts[0]
    if top in ("build", "sim_build"):
        raise _never(rel, "build output is written only by Siliconsmith tools")
    if last == "Makefile" or last.endswith(".mk"):
        raise _never(rel, "Makefiles are part of the Tiny Tapeout template and are never writable")
    if last == "conftest.py":
        raise _never(rel, "conftest.py can change how tests run, so it is not writable")
    if rel == "siliconsmith.toml":
        raise _never(rel, "it records the project name and front end and is written once")
    if rel == "info.yaml":
        raise _never(rel, "it is written by create_project; Tiny Tapeout metadata is not editable")
    if rel == "src/project.v":
        raise _never(rel, f"it is the generated tt_um_{name} wrapper, owned by Siliconsmith")
    if rel == "docs/verification.md":
        raise _never(rel, "it is generated by the status tool")
    if rel == "spec.yaml":
        return "spec"
    if top == "hdl":
        if frontend == "verilog":
            raise _err(
                f"{rel!r}: this is a verilog project; write the design to 'src/{name}.v' with "
                "write_rtl"
            )
        if last.endswith(".py"):
            return "rtl"
        raise _err(f"{rel!r}: files under hdl/ must be Python (.py) files")
    if top == "src":
        if rel == f"src/{name}.v":
            if frontend == "verilog":
                return "rtl"
            raise _err(
                f"{rel!r} is generated from your {frontend} source; edit 'hdl/{name}.py' with "
                "write_rtl instead"
            )
        raise _err(
            f"{rel!r} cannot be written; the only source file for this project is "
            + (f"'src/{name}.v'" if frontend == "verilog" else f"'hdl/{name}.py' (hdl/**/*.py)")
        )
    if top == "test":
        if last.endswith(".py"):
            return "test"
        raise _err(f"{rel!r}: only Python (.py) test files under test/ can be written")
    if top == "docs":
        if last.endswith(".md"):
            return "doc"
        raise _err(f"{rel!r}: only Markdown (.md) files under docs/ can be written")
    raise _err(
        f"{rel!r} is outside the write allow-list. Writable: "
        + (f"src/{name}.v" if frontend == "verilog" else "hdl/**/*.py")
        + ", spec.yaml, test/**/*.py, docs/**/*.md"
    )
