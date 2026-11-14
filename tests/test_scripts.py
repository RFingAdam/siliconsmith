# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

# Split so this file does not contain the literal name.
OLD_SERVER_CLASS = "Fast" + "MCP"


def _add(repo: Path, rel: str, data: bytes | str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)
    subprocess.run(["git", "add", rel], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


# check_api_guard


def test_api_guard_flags_banned_names(git_repo: Path, load_script: Any) -> None:
    mod = load_script("check_api_guard")
    _add(git_repo, "src/siliconsmith/a.py", f"from mcp.server.fastmcp import {OLD_SERVER_CLASS}\n")
    _add(git_repo, "src/siliconsmith/templates/tt/t.py", "x = BinaryValue(1)\n")
    _add(git_repo, "src/siliconsmith/b.py", "run(units='ns')\nfrom cocotb.runner import x\n")
    _add(git_repo, "src/siliconsmith/ok.py", "unit = 'ns'\n")
    problems = mod.scan(mod.tracked_files(git_repo))
    text = "\n".join(problems)
    assert OLD_SERVER_CLASS in text
    assert "BinaryValue" in text
    assert "units=" in text
    assert "cocotb.runner" in text
    assert "ok.py" not in text


def test_api_guard_units_word_boundary(git_repo: Path, load_script: Any) -> None:
    mod = load_script("check_api_guard")
    _add(git_repo, "src/siliconsmith/ok.py", "f(time_units=1, n_units=2)\n")
    assert mod.scan(mod.tracked_files(git_repo)) == []
    _add(git_repo, "src/siliconsmith/bad.py", 'Clock(dut.clk, 10, units="ns")\nx = f(units = 1)\n')
    assert len(mod.scan(mod.tracked_files(git_repo))) == 2


def test_api_guard_skips_skills_prose(git_repo: Path, load_script: Any) -> None:
    mod = load_script("check_api_guard")
    _add(git_repo, "src/siliconsmith/skills/x/SKILL.md", f"never use {OLD_SERVER_CLASS}\n")
    assert mod.scan(mod.tracked_files(git_repo)) == []


def test_api_guard_clean_on_this_repo(load_script: Any) -> None:
    mod = load_script("check_api_guard")
    assert mod.main() == 0


# check_no_binaries


def test_no_binaries_magic_and_size(tmp_path: Path, load_script: Any) -> None:
    mod = load_script("check_no_binaries")
    elf = tmp_path / "elf"
    elf.write_bytes(b"\x7fELF" + b"\0" * 20)
    gz = tmp_path / "gz"
    gz.write_bytes(b"\x1f\x8b\x08\0")
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 1_000_001)
    small = tmp_path / "small.txt"
    small.write_text("hello")
    assert mod.check_file(elf) == "ELF executable"
    assert mod.check_file(gz) == "gzip"
    assert "larger than 1 MB" in mod.check_file(big)
    assert mod.check_file(small) is None


def test_no_binaries_allowlist(tmp_path: Path, load_script: Any) -> None:
    mod = load_script("check_no_binaries")
    allow = tmp_path / "allow.txt"
    allow.write_text("# comment\n\nfoo/bar.bin\n")
    assert mod.load_allowlist(allow) == {"foo/bar.bin"}
    assert mod.load_allowlist(tmp_path / "missing") == set()


def test_no_binaries_clean_on_this_repo(load_script: Any) -> None:
    assert load_script("check_no_binaries").main() == 0


@pytest.mark.parametrize(
    ("name", "data", "label"),
    [
        ("pe", b"MZ\x90\x00" + b"\x01" * 20, "Windows PE"),
        ("macho", b"\xcf\xfa\xed\xfe" + b"\x01" * 20, "Mach-O"),
        ("macho2", b"\xfe\xed\xfa\xcf" + b"\x01" * 20, "Mach-O"),
        ("fat", b"\xca\xfe\xba\xbe" + b"\x01" * 20, "Mach-O"),
        ("wasm", b"\0asm\x01\0\0\0", "WebAssembly"),
        ("tar", b"a" * 257 + b"ustar" + b"\x20" * 20, "tar"),
        ("nul", b"text then \0 nul", "NUL"),
    ],
)
def test_no_binaries_more_magics(
    tmp_path: Path, load_script: Any, name: str, data: bytes, label: str
) -> None:
    mod = load_script("check_no_binaries")
    path = tmp_path / name
    path.write_bytes(data)
    assert label in (mod.check_file(path) or "")


# check_wheel


def test_wheel_missing_detection(tmp_path: Path, git_repo: Path, load_script: Any) -> None:
    mod = load_script("check_wheel")
    _add(git_repo, "src/siliconsmith/__init__.py", "")
    _add(git_repo, "src/siliconsmith/skills/a/SKILL.md", "x")
    _add(git_repo, "src/siliconsmith/__pycache__/x.pyc", b"\0")
    expected = mod.tracked_package_files(git_repo)
    assert "siliconsmith/skills/a/SKILL.md" in expected
    assert not any(name.endswith(".pyc") for name in expected)
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("siliconsmith/__init__.py", "")
    assert mod.missing_from_wheel(expected, wheel) == ["siliconsmith/skills/a/SKILL.md"]


def test_wheel_requires_every_skill_and_the_glossary(
    tmp_path: Path, git_repo: Path, load_script: Any
) -> None:
    mod = load_script("check_wheel")
    _add(git_repo, "src/siliconsmith/__init__.py", "")
    expected = mod.expected_files(git_repo)
    for name in ("chip-design-loop", "tt-rules", "amaranth-style", "myhdl-style", "cocotb2-tests"):
        assert f"siliconsmith/skills/{name}/SKILL.md" in expected
    assert "siliconsmith/skills/glossary.md" in expected
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("siliconsmith/__init__.py", "")
    assert len(mod.missing_from_wheel(expected, wheel)) == 6


def test_wheel_license_problems(tmp_path: Path, load_script: Any) -> None:
    mod = load_script("check_wheel")
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("x-1.dist-info/METADATA", "License-Expression: Apache-2.0\n")
    problems = mod.license_problems(wheel)
    assert "METADATA does not say License-Expression: AGPL-3.0-or-later" in problems
    assert len(problems) == 1 + len(mod.LICENSE_FILES)
    good = tmp_path / "g.whl"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("x-1.dist-info/METADATA", "Name: x\nLicense-Expression: AGPL-3.0-or-later\n")
        for name in mod.LICENSE_FILES:
            zf.writestr(f"x-1.dist-info/licenses/{name}", "")
    assert mod.license_problems(good) == []


def test_wheel_builds_and_is_complete(load_script: Any) -> None:
    assert load_script("check_wheel").main() == 0


# .gitignore


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("tests/fixtures/__pycache__/x.pyc", True),
        ("tests/fixtures/.env", True),
        ("src/siliconsmith/templates/tt/.DS_Store", True),
        ("tests/fixtures/x.vcd", False),
        ("src/siliconsmith/templates/tt/x.gds", False),
        ("tasks/todo.md", True),
    ],
)
def test_gitignore_rules(path: str, ignored: bool) -> None:
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=root)
    assert (proc.returncode == 0) is ignored


# check_trailers


@pytest.mark.parametrize(
    "line",
    [
        "Co-authored-by: Someone <a@b.c>",
        "Signed-off-by: Someone <a@b.c>",
        "co-authored-by: x",
        "Generated with a tool",
        "Co-Authored-By: x",
        "Co-authored-by : x",
        "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)",
        "Reviewed-by: Claude <noreply@anthropic.com>",
    ],
)
def test_trailers_flags(load_script: Any, line: str) -> None:
    mod = load_script("check_trailers")
    assert mod.offending_lines(f"subject\n\nbody\n{line}\n") == [line]


@pytest.mark.parametrize(
    "line",
    [
        "- Adds a trailer scan that fails on `Co-authored-by` or `Signed-off-by` lines.",
        "- `src/project.v` is generated with the wrapper generator.",
        "Reviewed-by: Alice <alice@example.com>",
    ],
)
def test_trailers_ignores_prose(load_script: Any, line: str) -> None:
    mod = load_script("check_trailers")
    assert mod.offending_lines(f"subject\n\n{line}\n") == []


def test_trailers_signoff_allowed_for_forks(load_script: Any) -> None:
    mod = load_script("check_trailers")
    text = "x\n\nSigned-off-by: A <a@b.c>\nCo-authored-by: B <b@b.c>\n"
    assert mod.offending_lines(text, allow_signoff=True) == ["Co-authored-by: B <b@b.c>"]
    assert not mod.is_fork_pr({})
    assert not mod.is_fork_pr({"HEAD_REPO": "o/r", "BASE_REPO": "o/r"})
    assert mod.is_fork_pr({"HEAD_REPO": "x/r", "BASE_REPO": "o/r"})


def test_trailers_clean_text(load_script: Any) -> None:
    mod = load_script("check_trailers")
    assert mod.offending_lines("Add feature\n\nSome details about signing off.\n") == []


def test_trailers_reads_git_log_and_pr_body(
    git_repo: Path, load_script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script("check_trailers")
    _add(git_repo, "a.txt", "1")
    _commit(git_repo, "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _add(git_repo, "a.txt", "2")
    _commit(git_repo, "clean change")
    monkeypatch.chdir(git_repo)
    monkeypatch.delenv("PR_BODY", raising=False)
    assert mod.main(["check_trailers.py", base]) == 0
    monkeypatch.setenv("PR_BODY", "text\nSigned-off-by: X <x@y.z>")
    assert mod.main(["check_trailers.py", base]) == 1
    monkeypatch.delenv("PR_BODY")
    _add(git_repo, "a.txt", "3")
    _commit(git_repo, "bad change\n\nCo-authored-by: Bot <bot@example.com>")
    assert mod.main(["check_trailers.py", base]) == 1


# check_cla

_CLA_BOX = (
    "- [x] I have read and agree to the [CLA](https://example.com/CLA.md) for this contribution."
)
_FORK = {"HEAD_REPO": "someone/siliconsmith", "BASE_REPO": "RFingAdam/siliconsmith"}
_SAME = {"HEAD_REPO": "RFingAdam/siliconsmith", "BASE_REPO": "RFingAdam/siliconsmith"}
_TEMPLATE = (
    Path(__file__).resolve().parent.parent / ".github" / "PULL_REQUEST_TEMPLATE.md"
).read_text()


def _run_cla(
    load_script: Any, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], body: str
) -> int:
    mod = load_script("check_cla")
    for key in ("HEAD_REPO", "BASE_REPO", "PR_BODY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**env, "PR_BODY": body}.items():
        monkeypatch.setenv(key, value)
    return int(mod.main())


def test_cla_fork_without_box_fails(load_script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_cla(load_script, monkeypatch, _FORK, _TEMPLATE) == 1
    assert _run_cla(load_script, monkeypatch, _FORK, "") == 1


def test_cla_fork_with_box_passes(load_script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_cla(load_script, monkeypatch, _FORK, f"## Checklist\n\n{_CLA_BOX}\n- [ ] x") == 0
    ticked = _TEMPLATE.replace("- [ ] I have read", "- [X] I have read", 1)
    assert _run_cla(load_script, monkeypatch, _FORK, ticked) == 0


def test_cla_other_ticked_box_does_not_count(
    load_script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "- [x] Tests pass locally.\n- [ ] I have read and agree to the CLA."
    assert _run_cla(load_script, monkeypatch, _FORK, body) == 1


def test_cla_same_repo_is_exempt(load_script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_cla(load_script, monkeypatch, _SAME, "") == 0


def test_cla_dependabot_is_exempt(load_script: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # Dependabot opens branches in this repository, so it takes the same-repo path.
    assert _run_cla(load_script, monkeypatch, _SAME, "Bumps x from 1 to 2.\nSigned-off-by: d") == 0


def test_cla_deleted_fork_still_needs_box(
    load_script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {"BASE_REPO": "RFingAdam/siliconsmith"}
    assert _run_cla(load_script, monkeypatch, env, "") == 1
    assert _run_cla(load_script, monkeypatch, env, _CLA_BOX) == 0


def test_cla_without_base_repo_is_an_error(
    load_script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run_cla(load_script, monkeypatch, {}, "") == 2


def test_pr_template_has_cla_box_first() -> None:
    boxes = [line for line in _TEMPLATE.splitlines() if line.startswith("- [ ]")]
    assert "CLA" in boxes[0]


def test_cla_box_inside_a_comment_does_not_count(
    load_script: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = f"<!--\n- [x] I have read and agree to the CLA\n-->\n{_TEMPLATE}"
    assert _run_cla(load_script, monkeypatch, _FORK, body) == 1
