# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Run one worker verb in a fresh, locked-down Docker container (PLAN.md section 5.1).

Every call is one `docker run` with a fixed argument list (a Python list, never a shell string).
The request goes in on stdin, and the worker writes one result file at a path the host chose
under the project's `build/` folder. Anything that comes back is untrusted: the result file is
read with a size cap and without following symlinks, and stdout and stderr are only kept as
short tails for error messages, never parsed.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import FileTooLargeError, MissingFileError, safe_read
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.worker_cache import ensure_worker_cache

DEFAULT_IMAGE = "ghcr.io/rfingadam/siliconsmith-toolchain:ctx-eec2cce80dc1"
IMAGE_ENV = "SILICONSMITH_IMAGE"

MAX_RESULT_BYTES = 1024 * 1024
STDIO_TAIL_BYTES = 16 * 1024
# Names under the project folder that are mounted read-only when they exist.
PROJECT_READONLY_ENTRIES = ("hdl", "src", "test", "spec.yaml", "info.yaml")
_FORBIDDEN_PATH_CHARS = {",": "a comma", ":": "a colon", "\n": "a newline", "\r": "a newline"}


class ToolchainError(RuntimeError):
    """Docker or the toolchain image is not usable. The message names the fix."""


class DockerUnavailableError(ToolchainError):
    pass


class ImageMissingError(ToolchainError):
    pass


class UnsafeProjectError(ToolchainError):
    """The project folder cannot be mounted safely."""


class ResultError(ValueError):
    """The worker's result file is missing, unsafe, too large or not valid JSON."""


def image_name(env: Mapping[str, str] | None = None) -> str:
    environ = os.environ if env is None else env
    return environ.get(IMAGE_ENV) or DEFAULT_IMAGE


def pull_hint(image: str) -> str:
    return f"docker pull {image}"


def check_mount_path(path: str, what: str = "path") -> str:
    """Refuse paths that would change the meaning of a `--mount` argument."""
    for char, label in _FORBIDDEN_PATH_CHARS.items():
        if char in path:
            raise UnsafeProjectError(
                f"the {what} {path!r} contains {label}, which Docker's --mount syntax cannot "
                "carry safely; move the project to a folder without it"
            )
    if "\x00" in path:
        raise UnsafeProjectError(f"the {what} contains a NUL byte")
    return path


def build_argv(
    *,
    container_name: str,
    image: str,
    verb: str,
    project_dir: Path,
    readonly_entries: Sequence[str],
    worker_cache: Path,
    user: tuple[int, int] | None,
    docker: str = "docker",
) -> list[str]:
    """The exact `docker run` command line for one worker call."""
    project = check_mount_path(str(project_dir), "project path")
    cache = check_mount_path(str(worker_cache), "worker cache path")
    argv = [
        docker, "run", "-i", "--rm", "--init",
        "--name", container_name,
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
    ]  # fmt: skip
    if user is not None:
        argv += ["--user", f"{user[0]}:{user[1]}"]
    for env in (
        "HOME=/tmp/home",
        "XDG_CACHE_HOME=/work/build/.cache",
        "CCACHE_DIR=/work/build/.cache/ccache",
        "PYTHONDONTWRITEBYTECODE=1",
        "AMARANTH_USE_YOSYS=builtin",
        "PYTHONPATH=/opt/ss",
    ):
        argv += ["-e", env]
    for entry in readonly_entries:
        if entry not in PROJECT_READONLY_ENTRIES:
            raise ValueError(f"{entry!r} is not a project entry that may be mounted")
        argv += ["--mount", f"type=bind,src={project}/{entry},dst=/work/{entry},readonly"]
    argv += ["--mount", f"type=bind,src={project}/build,dst=/work/build"]
    argv += ["--mount", f"type=bind,src={cache},dst=/opt/ss,readonly"]
    argv += [image, "python", "-m", "siliconsmith.worker", verb]
    return argv


def parse_rootless(security_options_json: str) -> bool:
    """True if `docker info --format '{{json .SecurityOptions}}'` says the daemon is rootless."""
    try:
        options = json.loads(security_options_json)
    except ValueError:
        return False
    return isinstance(options, list) and any(
        isinstance(item, str) and "name=rootless" in item.split(",") for item in options
    )


def _lstat_mode(path: Path) -> int | None:
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None


def prepare_build_dir(project_dir: Path) -> Path:
    """Create `<project>/build` if needed. Refuse a symlink there."""
    build = project_dir / "build"
    mode = _lstat_mode(build)
    if mode is None:
        build.mkdir(mode=0o755)
    elif stat.S_ISLNK(mode):
        raise UnsafeProjectError(f"{build} is a symlink; remove it so Siliconsmith can use build/")
    elif not stat.S_ISDIR(mode):
        raise UnsafeProjectError(f"{build} exists but is not a directory")
    return build


def readonly_entries(project_dir: Path) -> list[str]:
    entries = []
    for name in PROJECT_READONLY_ENTRIES:
        mode = _lstat_mode(project_dir / name)
        if mode is None:
            continue
        if stat.S_ISLNK(mode):
            raise UnsafeProjectError(f"{project_dir / name} is a symlink; replace it with the file")
        entries.append(name)
    return entries


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def make_run_dir(build: Path, run_id: str) -> None:
    """Create `build/.ss/<run_id>` without following symlinks that container code planted."""
    fd = os.open(build, _DIR_FLAGS)
    try:
        for name in (".ss", run_id):
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o755, dir_fd=fd)
            child = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError as exc:
        raise UnsafeProjectError(f"cannot create the run folder under {build}: {exc}") from exc
    finally:
        os.close(fd)


def remove_run_dirs(build: Path) -> None:
    """Delete `build/.ss` (the worker's run folders) without following any symlink.

    Container code can replace `build/.ss`, or anything inside it, with a symlink. Everything
    here goes through file descriptors: `build` is opened with O_NOFOLLOW, a `.ss` that is not a
    real directory is only unlinked, and `rmtree` on a descriptor never follows links.
    """
    remove_in_build(build, ".ss")


def remove_in_build(build: Path, name: str) -> None:
    """Delete `build/<name>` (a file, a folder or a symlink) without following any symlink.

    Container code can replace the entry, or anything inside it, with a symlink. Everything here
    goes through file descriptors: `build` is opened with O_NOFOLLOW, an entry that is not a real
    directory is only unlinked, and `rmtree` on a descriptor never follows links.
    """
    try:
        fd = os.open(build, _DIR_FLAGS)
    except OSError:
        return
    try:
        try:
            mode = os.lstat(name, dir_fd=fd).st_mode
        except OSError:
            return
        with contextlib.suppress(OSError):
            if stat.S_ISDIR(mode):
                shutil.rmtree(name, ignore_errors=True, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
    finally:
        os.close(fd)


def read_result(build: Path, run_id: str) -> dict[str, Any] | None:
    """Read `build/.ss/<run_id>/result.json`. None if there is no file; ResultError if it is bad.

    This is `paths.safe_read`: every folder on the way is opened with O_NOFOLLOW, and the file
    must be a regular file with a single hard link and at most 1 MiB, so container code cannot
    point this read at a host file.
    """
    rel = f"{build.name}/.ss/{run_id}/result.json"
    try:
        data = safe_read(build.parent, rel, MAX_RESULT_BYTES, allow_hidden=True)
    except MissingFileError:
        return None
    except FileTooLargeError:
        raise ResultError(f"result.json is larger than {MAX_RESULT_BYTES} bytes") from None
    except ToolError as exc:
        raise ResultError(f"the result path is unsafe ({exc})") from exc
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError) as exc:
        # RecursionError: a deeply nested file well under the size cap.
        raise ResultError("result.json is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ResultError("result.json is not a JSON object")
    return parsed


class _TailReader(threading.Thread):
    """Drain a pipe, keeping only the last `cap` bytes, so a chatty container cannot block."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self._buffer = bytearray()
        self._truncated = False

    def run(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            while chunk := self._stream.read1(65536):  # type: ignore[attr-defined]
                self._buffer += chunk
                if len(self._buffer) > 2 * self._cap:
                    del self._buffer[: -self._cap]
                    self._truncated = True

    def tail(self) -> str:
        data = bytes(self._buffer[-self._cap :])
        text = data.decode("utf-8", errors="replace")
        return ("… " + text) if self._truncated or len(self._buffer) > self._cap else text


def _feed(stream: IO[bytes], data: bytes) -> None:
    with contextlib.suppress(OSError, ValueError):
        stream.write(data)
        stream.close()


@dataclass(frozen=True)
class HostStatus:
    """What the host can see about Docker and the image, without starting a container."""

    docker_available: bool
    server_version: str | None = None
    rootless: bool | None = None
    image_present: bool = False
    image_id: str | None = None
    repo_digest: str | None = None
    size_bytes: int | None = None
    error: str | None = None


class DockerRunner:
    """`Runner` that starts one container per call."""

    def __init__(
        self,
        image: str | None = None,
        *,
        docker: str = "docker",
        worker_cache: Path | None = None,
    ) -> None:
        self.image = image or image_name()
        self.docker = docker
        self._worker_cache = worker_cache
        self._rootless: bool | None = None
        self.last_container: str | None = None
        self._image_present = False
        self.gone_timeout_s = 15.0

    # -- host-side checks -------------------------------------------------------------------

    def _docker(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.docker, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise DockerUnavailableError(
                "the docker command was not found; install Docker Engine and make sure "
                "`docker` is on PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerUnavailableError(
                f"`docker {args[0]}` did not answer within {timeout:.0f} s; is the Docker "
                "daemon running?"
            ) from exc

    def is_rootless(self) -> bool:
        if self._rootless is None:
            proc = self._docker("info", "--format", "{{json .SecurityOptions}}")
            if proc.returncode != 0:
                raise DockerUnavailableError(
                    "cannot talk to the Docker daemon; start Docker, and make sure your user "
                    f"may run `docker` ({_first_line(proc.stderr)})"
                )
            self._rootless = parse_rootless(proc.stdout)
        return self._rootless

    def host_status(self) -> HostStatus:
        """Docker server version, rootless mode and image details. Never raises."""
        try:
            proc = self._docker("version", "--format", "{{.Server.Version}}")
            if proc.returncode != 0:
                return HostStatus(False, error=_first_line(proc.stderr) or "docker is not usable")
            version = proc.stdout.strip() or None
            rootless = self.is_rootless()
            inspect = self._docker("image", "inspect", self.image)
        except ToolchainError as exc:
            return HostStatus(False, error=str(exc))
        if inspect.returncode != 0:
            return HostStatus(True, version, rootless)
        try:
            info = json.loads(inspect.stdout)[0]
        except (ValueError, IndexError, TypeError):
            return HostStatus(True, version, rootless, error="unreadable docker image inspect")
        digest = self._registry_digest(info)
        return HostStatus(
            True,
            version,
            rootless,
            image_present=True,
            image_id=info.get("Id"),
            repo_digest=digest,
            size_bytes=info.get("Size"),
        )

    def _registry_digest(self, info: Mapping[str, Any]) -> str | None:
        """The digest this image has in a registry, or None for a local build.

        With the classic image store, `RepoDigests` only lists digests that a pull or push
        recorded. The containerd store (the default on new Docker installs) also lists the local
        manifest digest of a build nobody pushed, and that digest equals the image id. In that
        case ask the registry whether it knows the digest; offline or unknown means None.
        """
        name = self.image.rsplit("/", 1)[-1]
        repo = self.image.rsplit(":", 1)[0] if ":" in name else self.image
        entries = info.get("RepoDigests") or []
        found = [
            e.split("@", 1)[1] for e in entries if isinstance(e, str) and e.startswith(repo + "@")
        ]
        if not found:
            return None
        digest = found[0]
        if digest != info.get("Id"):
            return digest
        try:
            proc = self._docker("manifest", "inspect", f"{repo}@{digest}", timeout=20)
        except ToolchainError:
            return None
        return digest if proc.returncode == 0 else None

    # -- the call itself --------------------------------------------------------------------

    def worker_cache(self) -> Path:
        if self._worker_cache is None:
            self._worker_cache = ensure_worker_cache()
        return self._worker_cache

    def run(
        self,
        verb: str,
        request: Mapping[str, Any],
        *,
        project_dir: Path,
        timeout_s: float,
    ) -> WorkerOutcome:
        project_dir = Path(os.path.abspath(project_dir))
        check_mount_path(str(project_dir), "project path")
        rootless = self.is_rootless()
        if not rootless and os.getuid() == 0:
            raise ToolchainError(
                "Siliconsmith is running as root with a rootful Docker daemon, which would let "
                "container code leave root-owned files in your project; run it as a normal "
                "user, or use rootless Docker"
            )
        self._ensure_image()
        build = prepare_build_dir(project_dir)
        run_id = uuid.uuid4().hex
        make_run_dir(build, run_id)
        name = f"ss-{run_id}"
        argv = build_argv(
            container_name=name,
            image=self.image,
            verb=verb,
            project_dir=project_dir,
            readonly_entries=readonly_entries(project_dir),
            worker_cache=self.worker_cache(),
            user=None if rootless else (os.getuid(), os.getgid()),
            docker=self.docker,
        )
        run_dir = f"/work/build/.ss/{run_id}"
        payload = json.dumps(
            {**request, "run_dir": run_dir, "result_path": f"{run_dir}/result.json"}
        ).encode()
        self.last_container = name
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError as exc:
            raise DockerUnavailableError(
                "the docker command was not found; install Docker Engine"
            ) from exc
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        threading.Thread(target=_feed, args=(proc.stdin, payload), daemon=True).start()
        out_reader = _TailReader(proc.stdout, STDIO_TAIL_BYTES)
        err_reader = _TailReader(proc.stderr, STDIO_TAIL_BYTES)
        out_reader.start()
        err_reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill(name)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self._ensure_gone(name)
        out_reader.join(5)
        err_reader.join(5)
        stdout_tail, stderr_tail = out_reader.tail(), err_reader.tail()
        result: dict[str, Any] | None = None
        try:
            result = read_result(build, run_id)
        except ResultError as exc:
            stderr_tail = f"[siliconsmith] result rejected: {exc}\n{stderr_tail}"
        return WorkerOutcome(
            run_id=run_id,
            exit_code=proc.returncode,
            result=result,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            timed_out=timed_out,
        )

    def _kill(self, name: str) -> None:
        with contextlib.suppress(ToolchainError, OSError):
            self._docker("kill", name, timeout=30)

    def _ensure_image(self) -> None:
        """Decide on the host, never from container output, that the image is here."""
        if self._image_present:
            return
        proc = self._docker("image", "inspect", "--format", "{{.Id}}", self.image)
        if proc.returncode != 0:
            raise ImageMissingError(
                f"the toolchain image {self.image} is not on this machine; "
                f"run `{pull_hint(self.image)}`"
            )
        self._image_present = True

    def _ensure_gone(self, name: str) -> None:
        """After a timeout, make sure the container really is gone (remove it if it is not)."""
        deadline = time.monotonic() + self.gone_timeout_s
        removed = False
        while True:
            proc = self._docker("ps", "-a", "-q", "--filter", f"name=^/{name}$")
            if proc.returncode != 0:
                raise ToolchainError(f"cannot check whether container {name} stopped")
            if not proc.stdout.strip():
                return
            if time.monotonic() > deadline:
                raise ToolchainError(
                    f"container {name} is still running after a timeout; remove it with "
                    f"`docker rm -f {name}`"
                )
            if not removed:
                with contextlib.suppress(ToolchainError):
                    self._docker("rm", "-f", name, timeout=30)
                removed = True
            time.sleep(0.5)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


__all__ = [
    "DEFAULT_IMAGE",
    "IMAGE_ENV",
    "DockerRunner",
    "DockerUnavailableError",
    "HostStatus",
    "ImageMissingError",
    "ResultError",
    "ToolchainError",
    "UnsafeProjectError",
    "build_argv",
    "check_mount_path",
    "image_name",
    "parse_rootless",
    "pull_hint",
    "read_result",
]
