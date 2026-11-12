# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The in-container worker, run on the host with the standard library only."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from siliconsmith.runners.worker_cache import ensure_worker_cache
from siliconsmith.worker import __main__ as worker_main
from siliconsmith.worker.__main__ import RequestError, check_result_path, main


@pytest.fixture
def build_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "build"
    path.mkdir(parents=True)
    return path


def invoke(verb: str, request: object, build_dir: Path) -> int:
    return main([verb], stdin=io.StringIO(json.dumps(request)), build_dir=str(build_dir))


def test_selftest_writes_one_result_file(build_dir: Path) -> None:
    result_path = build_dir / ".ss" / "r1" / "result.json"
    assert invoke("selftest", {"result_path": str(result_path)}, build_dir) == 0
    data = json.loads(result_path.read_text())
    assert data["ok"] is True and data["verb"] == "selftest"
    assert isinstance(data["uid"], int)
    assert data["build_writable"] is False  # /work/build does not exist on the host
    assert [p.name for p in result_path.parent.iterdir()] == ["result.json"]


def test_info_verb_reports_every_key(build_dir: Path) -> None:
    result_path = build_dir / "result.json"
    assert invoke("info", {"result_path": str(result_path)}, build_dir) == 0
    versions = json.loads(result_path.read_text())["versions"]
    assert set(versions) == {
        "iverilog",
        "verilator",
        "yosys",
        "amaranth",
        "amaranth_yosys",
        "myhdl",
        "cocotb",
        "python",
        "pywellen",
    }
    assert versions["python"] == sys.version.split()[0]


def test_unknown_verb_still_writes_an_error_result(build_dir: Path) -> None:
    result_path = build_dir / "result.json"
    assert invoke("nope", {"result_path": str(result_path)}, build_dir) == 0
    data = json.loads(result_path.read_text())
    assert data["ok"] is False
    assert data["error"]["kind"] == "unknown_verb"


def test_verb_failure_becomes_an_error_result(
    build_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("kaput")

    monkeypatch.setitem(worker_main.VERBS, "info", boom)
    result_path = build_dir / "result.json"
    assert invoke("info", {"result_path": str(result_path)}, build_dir) == 0
    data = json.loads(result_path.read_text())
    assert data["ok"] is False
    assert data["error"] == {"kind": "RuntimeError", "message": "kaput"}


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/work/build",
        "/work/buildx/result.json",
        "/work/build/../x/result.json",
        "/work/build/./result.json",
        "relative/result.json",
        "",
        "/work/build/a\0b",
    ],
)
def test_result_path_outside_build_is_refused(bad: str) -> None:
    with pytest.raises(RequestError):
        check_result_path(bad)


def test_result_path_must_be_a_string() -> None:
    with pytest.raises(RequestError):
        check_result_path(None)
    with pytest.raises(RequestError):
        check_result_path(5)


def test_good_result_path() -> None:
    assert check_result_path("/work/build/.ss/x/result.json") == "/work/build/.ss/x/result.json"


def test_bad_requests_exit_with_2_and_write_nothing(build_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    assert invoke("info", {"result_path": str(outside)}, build_dir) == 2
    assert not outside.exists()
    assert main(["info"], stdin=io.StringIO("not json"), build_dir=str(build_dir)) == 2
    assert main(["info"], stdin=io.StringIO("[1]"), build_dir=str(build_dir)) == 2
    assert main([], stdin=io.StringIO("{}"), build_dir=str(build_dir)) == 2


def test_no_leftover_temp_files_on_success(build_dir: Path) -> None:
    invoke("selftest", {"result_path": str(build_dir / "result.json")}, build_dir)
    assert [p.name for p in build_dir.iterdir()] == ["result.json"]


ISOLATION_SCRIPT = textwrap.dedent(
    """\
    import io, json, sys
    # Block everything the container does not have. A None entry makes `import x` fail.
    for name in ("mcp", "pydantic", "yaml", "anyio", "siliconsmith.server", "siliconsmith.context",
                 "siliconsmith.result", "siliconsmith.tools", "siliconsmith.runners",
                 "siliconsmith.cli"):
        sys.modules[name] = None
    sys.path.insert(0, sys.argv[1])
    from siliconsmith.worker.__main__ import main
    request = {"result_path": sys.argv[2] + "/result.json"}
    code = main(["selftest"], stdin=io.StringIO(json.dumps(request)), build_dir=sys.argv[2])
    sys.exit(code)
    """
)


def test_worker_runs_with_host_packages_blocked(tmp_path: Path) -> None:
    """The worker folder, copied as the container sees it, imports with only the stdlib."""
    cache = ensure_worker_cache(home=tmp_path / "cache")
    build = tmp_path / "b"
    build.mkdir()
    proc = subprocess.run(
        [sys.executable, "-I", "-c", ISOLATION_SCRIPT, str(cache), str(build)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads((build / "result.json").read_text())["ok"] is True


def test_worker_modules_import_only_stdlib_at_top_level() -> None:
    import ast

    worker = Path(worker_main.__file__).parent
    stdlib = set(sys.stdlib_module_names)
    for path in worker.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top in stdlib or name.startswith("siliconsmith.worker"), (path.name, name)
