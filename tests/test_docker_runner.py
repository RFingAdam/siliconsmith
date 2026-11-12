# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Docker runner: argument list, path checks, result reader and the run loop (fake `docker`)."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from siliconsmith.runners import docker as dk
from siliconsmith.runners.docker import (
    DockerRunner,
    DockerUnavailableError,
    ImageMissingError,
    ResultError,
    ToolchainError,
    UnsafeProjectError,
    build_argv,
    check_mount_path,
    parse_rootless,
    read_result,
)

IMAGE = "ghcr.io/example/toolchain:ctx-abc"
CACHE = Path("/cache/worker/abc")


def argv_for(**overrides: object) -> list[str]:
    kwargs: dict[str, object] = {
        "container_name": "ss-1234",
        "image": IMAGE,
        "verb": "info",
        "project_dir": Path("/proj"),
        "readonly_entries": ["hdl", "src", "test", "spec.yaml", "info.yaml"],
        "worker_cache": CACHE,
        "user": (1000, 1001),
    }
    kwargs.update(overrides)
    return build_argv(**kwargs)  # type: ignore[arg-type]


def test_argv_is_exactly_the_plan() -> None:
    assert argv_for() == [
        "docker", "run", "-i", "--rm", "--init",
        "--name", "ss-1234",
        "--pull", "never",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "512",
        "--memory", "4g",
        "--memory-swap", "4g",
        "--cpus", "2",
        "--ulimit", "fsize=2147483648",
        "--ulimit", "nofile=4096",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g",
        "--user", "1000:1001",
        "-e", "HOME=/tmp/home",
        "-e", "XDG_CACHE_HOME=/work/build/.cache",
        "-e", "CCACHE_DIR=/work/build/.cache/ccache",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "AMARANTH_USE_YOSYS=builtin",
        "-e", "PYTHONPATH=/opt/ss",
        "--mount", "type=bind,src=/proj/hdl,dst=/work/hdl,readonly",
        "--mount", "type=bind,src=/proj/src,dst=/work/src,readonly",
        "--mount", "type=bind,src=/proj/test,dst=/work/test,readonly",
        "--mount", "type=bind,src=/proj/spec.yaml,dst=/work/spec.yaml,readonly",
        "--mount", "type=bind,src=/proj/info.yaml,dst=/work/info.yaml,readonly",
        "--mount", "type=bind,src=/proj/build,dst=/work/build",
        "--mount", "type=bind,src=/cache/worker/abc,dst=/opt/ss,readonly",
        IMAGE, "python", "-m", "siliconsmith.worker", "info",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--pids-limit", "512"),
        ("--memory", "4g"),
        ("--memory-swap", "4g"),
        ("--cpus", "2"),
        ("--pull", "never"),
        ("--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g"),
    ],
)
def test_argv_flag(flag: str, value: str) -> None:
    argv = argv_for()
    assert argv[argv.index(flag) + 1] == value


def test_argv_read_only_root_init_and_ulimits() -> None:
    argv = argv_for()
    assert "--read-only" in argv
    assert "--init" in argv
    assert "-i" in argv
    assert "--rm" in argv
    ulimits = [argv[i + 1] for i, item in enumerate(argv) if item == "--ulimit"]
    assert ulimits == ["fsize=2147483648", "nofile=4096"]


def test_argv_never_mounts_the_docker_socket_or_publishes_ports() -> None:
    text = " ".join(argv_for())
    assert "docker.sock" not in text
    assert "--privileged" not in text
    assert " -p " not in text
    assert "--network host" not in text


def test_argv_rootless_has_no_user_flag() -> None:
    assert "--user" not in argv_for(user=None)
    assert argv_for(user=(5, 6))[argv_for(user=(5, 6)).index("--user") + 1] == "5:6"


def test_argv_only_mounts_entries_that_exist() -> None:
    argv = argv_for(readonly_entries=["hdl"])
    mounts = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"]
    assert mounts == [
        "type=bind,src=/proj/hdl,dst=/work/hdl,readonly",
        "type=bind,src=/proj/build,dst=/work/build",
        "type=bind,src=/cache/worker/abc,dst=/opt/ss,readonly",
    ]


def test_only_build_and_not_readonly_mounts_are_writable() -> None:
    argv = argv_for()
    mounts = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"]
    writable = [m for m in mounts if not m.endswith(",readonly")]
    assert writable == ["type=bind,src=/proj/build,dst=/work/build"]


def test_argv_refuses_unknown_readonly_entry() -> None:
    with pytest.raises(ValueError, match="build"):
        argv_for(readonly_entries=["build"])
    with pytest.raises(ValueError, match=r"\.\."):
        argv_for(readonly_entries=["../etc"])


@pytest.mark.parametrize("bad", ["/a,b", "/a:b", "/a\nb", "/a\rb", "/a\0b"])
def test_project_path_rejected(bad: str) -> None:
    with pytest.raises(UnsafeProjectError):
        argv_for(project_dir=Path(bad))
    with pytest.raises(UnsafeProjectError):
        check_mount_path(bad)


def test_worker_cache_path_rejected() -> None:
    with pytest.raises(UnsafeProjectError, match="worker cache"):
        argv_for(worker_cache=Path("/x,y"))


def test_plain_paths_with_spaces_are_fine() -> None:
    assert "src=/my proj/build,dst=/work/build" in " ".join(argv_for(project_dir=Path("/my proj")))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('["name=seccomp,profile=builtin","name=rootless"]', True),
        ('["name=rootless","name=cgroupns"]', True),
        ('["name=seccomp,profile=builtin","name=cgroupns"]', False),
        ('["name=seccomp,profile=builtin,name=rootless"]', True),
        ("[]", False),
        ("null", False),
        ("not json", False),
        ('["name=notrootless"]', False),
    ],
)
def test_parse_rootless(text: str, expected: bool) -> None:
    assert parse_rootless(text) is expected


# -- the result reader ----------------------------------------------------------------------


@pytest.fixture
def build(tmp_path: Path) -> Path:
    path = tmp_path / "build"
    path.mkdir()
    return path


def run_folder(build: Path, run_id: str = "r1") -> Path:
    folder = build / ".ss" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_read_result_ok(build: Path) -> None:
    (run_folder(build) / "result.json").write_text('{"ok": true, "n": 3}')
    assert read_result(build, "r1") == {"ok": True, "n": 3}


def test_read_result_missing_is_none(build: Path) -> None:
    assert read_result(build, "r1") is None
    run_folder(build)
    assert read_result(build, "r1") is None


def test_read_result_rejects_symlinked_file(build: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret": 1}')
    (run_folder(build) / "result.json").symlink_to(secret)
    with pytest.raises(ResultError, match="unsafe"):
        read_result(build, "r1")


def test_read_result_rejects_symlinked_run_folder(build: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text('{"secret": 1}')
    (build / ".ss").mkdir()
    (build / ".ss" / "r1").symlink_to(outside)
    with pytest.raises(ResultError, match="unsafe"):
        read_result(build, "r1")


def test_read_result_rejects_symlinked_dot_ss(build: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "r1").mkdir(parents=True)
    (outside / "r1" / "result.json").write_text("{}")
    (build / ".ss").symlink_to(outside)
    with pytest.raises(ResultError, match="unsafe"):
        read_result(build, "r1")


def test_read_result_rejects_symlinked_build(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / ".ss" / "r1").mkdir(parents=True)
    (real / ".ss" / "r1" / "result.json").write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ResultError):
        read_result(link, "r1")


def test_read_result_rejects_hard_link(build: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret": 1}')
    os.link(secret, run_folder(build) / "result.json")
    with pytest.raises(ResultError, match="hard link"):
        read_result(build, "r1")


def test_read_result_rejects_oversize(build: Path) -> None:
    (run_folder(build) / "result.json").write_text(json.dumps({"pad": "x" * dk.MAX_RESULT_BYTES}))
    with pytest.raises(ResultError, match="larger"):
        read_result(build, "r1")


def test_read_result_accepts_exactly_the_cap(build: Path) -> None:
    body = json.dumps({"p": ""})
    pad = "x" * (dk.MAX_RESULT_BYTES - len(body))
    text = json.dumps({"p": pad})
    assert len(text) == dk.MAX_RESULT_BYTES
    (run_folder(build) / "result.json").write_text(text)
    assert read_result(build, "r1") == {"p": pad}


@pytest.mark.parametrize("text", ["{not json", "", "[1, 2]", '"str"', "null"])
def test_read_result_rejects_invalid(build: Path, text: str) -> None:
    (run_folder(build) / "result.json").write_text(text)
    with pytest.raises(ResultError):
        read_result(build, "r1")


def test_read_result_rejects_fifo(build: Path) -> None:
    os.mkfifo(run_folder(build) / "result.json")
    with pytest.raises(ResultError, match="regular"):
        read_result(build, "r1")


# -- the build folder and run folder ---------------------------------------------------------


def test_prepare_build_dir_creates_and_reuses(tmp_path: Path) -> None:
    assert dk.prepare_build_dir(tmp_path) == tmp_path / "build"
    assert (tmp_path / "build").is_dir()
    assert dk.prepare_build_dir(tmp_path) == tmp_path / "build"


def test_prepare_build_dir_refuses_symlink(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "build").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(UnsafeProjectError, match="symlink"):
        dk.prepare_build_dir(tmp_path)


def test_prepare_build_dir_refuses_file(tmp_path: Path) -> None:
    (tmp_path / "build").write_text("")
    with pytest.raises(UnsafeProjectError, match="not a directory"):
        dk.prepare_build_dir(tmp_path)


def test_make_run_dir_refuses_planted_symlink(build: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (build / ".ss").symlink_to(outside)
    with pytest.raises(UnsafeProjectError):
        dk.make_run_dir(build, "r1")
    assert list(outside.iterdir()) == []


def test_make_run_dir_creates_nested(build: Path) -> None:
    dk.make_run_dir(build, "r9")
    assert (build / ".ss" / "r9").is_dir()


def test_readonly_entries_lists_only_existing_and_refuses_symlinks(tmp_path: Path) -> None:
    (tmp_path / "hdl").mkdir()
    (tmp_path / "spec.yaml").write_text("")
    assert dk.readonly_entries(tmp_path) == ["hdl", "spec.yaml"]
    (tmp_path / "src").symlink_to(tmp_path / "hdl")
    with pytest.raises(UnsafeProjectError, match="symlink"):
        dk.readonly_entries(tmp_path)


# -- image name -----------------------------------------------------------------------------


def test_image_name_env_override() -> None:
    assert dk.image_name({}) == dk.DEFAULT_IMAGE
    assert dk.image_name({"SILICONSMITH_IMAGE": "x/y:z"}) == "x/y:z"
    assert dk.image_name({"SILICONSMITH_IMAGE": ""}) == dk.DEFAULT_IMAGE


def test_default_image_shape() -> None:
    assert dk.DEFAULT_IMAGE.startswith("ghcr.io/rfingadam/siliconsmith-toolchain:ctx-")


# -- the run loop, against a fake `docker` ---------------------------------------------------

FAKE_DOCKER = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys, time
    args = sys.argv[1:]
    log = open(os.environ["FAKE_DOCKER_LOG"], "a")
    log.write(json.dumps(args) + "\\n")
    log.close()
    mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
    if args[0] == "info":
        print(os.environ.get("FAKE_DOCKER_SECURITY", '["name=seccomp,profile=builtin"]'))
    elif args[0] == "kill":
        pass
    elif args[0] == "run":
        request = json.load(sys.stdin)
        if mode == "missing":
            print("Unable to find image 'x' locally", file=sys.stderr)
            sys.exit(125)
        if mode == "sleep":
            time.sleep(60)
        if mode == "chatty":
            sys.stdout.write("A" * 200000 + "END-OUT")
            sys.stderr.write("B" * 200000 + "END-ERR")
        src = [a for a in args if a.endswith("dst=/work/build")][0].split("src=")[1].split(",")[0]
        host_path = request["result_path"].replace("/work/build", src, 1)
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        if mode == "symlink":
            os.symlink("/etc/passwd", host_path)
        elif mode != "noresult":
            with open(host_path, "w") as f:
                json.dump({{"ok": True, "echo": request}}, f)
        sys.exit(int(os.environ.get("FAKE_DOCKER_EXIT", "0")))
    """
)


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    script = tmp_path / "fakebin" / "docker"
    script.parent.mkdir()
    script.write_text(FAKE_DOCKER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return str(script), log


def logged(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "proj"
    (path / "hdl").mkdir(parents=True)
    (path / "spec.yaml").write_text("")
    return path


@pytest.fixture
def runner(fake_docker: tuple[str, Path], tmp_path: Path) -> DockerRunner:
    cache = tmp_path / "wcache"
    cache.mkdir()
    return DockerRunner(IMAGE, docker=fake_docker[0], worker_cache=cache)


def test_run_round_trip(runner: DockerRunner, fake_docker: tuple[str, Path], project: Path) -> None:
    outcome = runner.run("info", {"a": 1}, project_dir=project, timeout_s=30)
    assert outcome.exit_code == 0 and not outcome.timed_out
    assert outcome.result is not None
    echo = outcome.result["echo"]
    assert echo["a"] == 1
    assert echo["result_path"].startswith("/work/build/.ss/")
    assert echo["result_path"].endswith("/result.json")
    assert echo["run_dir"] == echo["result_path"].rsplit("/", 1)[0]
    calls = logged(fake_docker[1])
    assert calls[0][0] == "info"
    run_call = calls[1]
    assert run_call[:2] == ["run", "-i"]
    assert run_call[run_call.index("--name") + 1] == runner.last_container
    assert "--user" in run_call
    assert run_call[-5:] == [IMAGE, "python", "-m", "siliconsmith.worker", "info"]
    assert (project / "build" / ".ss").is_dir()


def test_run_is_one_container_per_call_and_caches_rootless(
    runner: DockerRunner, fake_docker: tuple[str, Path], project: Path
) -> None:
    runner.run("info", {}, project_dir=project, timeout_s=30)
    first = runner.last_container
    runner.run("info", {}, project_dir=project, timeout_s=30)
    assert runner.last_container != first
    verbs = [c[0] for c in logged(fake_docker[1])]
    assert verbs.count("info") == 1
    assert verbs.count("run") == 2


def test_run_rootless_omits_user(
    runner: DockerRunner,
    fake_docker: tuple[str, Path],
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_SECURITY", '["name=rootless"]')
    runner.run("info", {}, project_dir=project, timeout_s=30)
    run_call = next(c for c in logged(fake_docker[1]) if c[0] == "run")
    assert "--user" not in run_call


def test_run_times_out_and_kills(
    runner: DockerRunner,
    fake_docker: tuple[str, Path],
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_MODE", "sleep")
    outcome = runner.run("info", {}, project_dir=project, timeout_s=1)
    assert outcome.timed_out
    assert outcome.result is None
    kills = [c for c in logged(fake_docker[1]) if c[0] == "kill"]
    assert kills == [["kill", runner.last_container]]


def test_run_keeps_only_tails_of_noisy_output(
    runner: DockerRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_MODE", "chatty")
    outcome = runner.run("info", {}, project_dir=project, timeout_s=30)
    assert outcome.stdout_tail.endswith("END-OUT")
    assert outcome.stderr_tail.endswith("END-ERR")
    assert len(outcome.stdout_tail) <= dk.STDIO_TAIL_BYTES + 4
    assert outcome.stdout_tail.startswith("…")
    assert outcome.result is not None  # stdout was never parsed as a result


def test_run_rejects_symlinked_result(
    runner: DockerRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_MODE", "symlink")
    outcome = runner.run("info", {}, project_dir=project, timeout_s=30)
    assert outcome.result is None
    assert "result rejected" in outcome.stderr_tail


def test_run_without_result_file(
    runner: DockerRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_MODE", "noresult")
    monkeypatch.setenv("FAKE_DOCKER_EXIT", "3")
    outcome = runner.run("info", {}, project_dir=project, timeout_s=30)
    assert outcome.result is None and outcome.exit_code == 3


def test_run_missing_image_names_the_fix(
    runner: DockerRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_MODE", "missing")
    with pytest.raises(ImageMissingError, match=f"docker pull {IMAGE}"):
        runner.run("info", {}, project_dir=project, timeout_s=30)


def test_run_without_docker_installed(project: Path, tmp_path: Path) -> None:
    runner = DockerRunner(IMAGE, docker=str(tmp_path / "nope"), worker_cache=tmp_path)
    with pytest.raises(DockerUnavailableError, match="install Docker"):
        runner.run("info", {}, project_dir=project, timeout_s=5)


def test_run_refuses_bad_project_path(runner: DockerRunner, tmp_path: Path) -> None:
    bad = tmp_path / "a,b"
    bad.mkdir()
    with pytest.raises(UnsafeProjectError):
        runner.run("info", {}, project_dir=bad, timeout_s=5)


def test_run_refuses_symlinked_build(runner: DockerRunner, project: Path, tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    (project / "build").symlink_to(tmp_path / "other")
    with pytest.raises(UnsafeProjectError, match="symlink"):
        runner.run("info", {}, project_dir=project, timeout_s=5)


def test_host_status_without_docker(tmp_path: Path) -> None:
    status = DockerRunner(IMAGE, docker=str(tmp_path / "nope")).host_status()
    assert not status.docker_available
    assert status.error and "install Docker" in status.error


def test_errors_share_a_base() -> None:
    assert issubclass(ImageMissingError, ToolchainError)
    assert issubclass(DockerUnavailableError, ToolchainError)
