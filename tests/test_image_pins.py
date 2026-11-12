# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The image name, the README and the files under docker/ must agree with each other."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

from siliconsmith import _imagetag
from siliconsmith.runners.docker import DEFAULT_IMAGE

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="needs a git checkout",
)


def current_tag() -> str:
    return f"ctx-{_imagetag.compute(REPO_ROOT)}"


def test_default_image_ends_with_the_current_tag() -> None:
    assert f"ghcr.io/rfingadam/siliconsmith-toolchain:{current_tag()}" == DEFAULT_IMAGE


def test_readme_image_tag_current() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    section = re.search(r"^## Toolchain image\n(.*?)(?=^## |\Z)", readme, re.S | re.M)
    assert section, "README.md needs a '## Toolchain image' section"
    assert f"siliconsmith-toolchain:{current_tag()}" in section.group(1)
    assert re.search(r"\b\d+ MB\b", section.group(1)), "the section must state the image size"


def test_docker_copy_of_third_party_licenses_is_identical() -> None:
    root = (REPO_ROOT / "THIRD_PARTY_LICENSES.md").read_bytes()
    assert (REPO_ROOT / "docker" / "THIRD_PARTY_LICENSES.md").read_bytes() == root, (
        "docker/THIRD_PARTY_LICENSES.md must be a copy of THIRD_PARTY_LICENSES.md "
        "(the image build context is docker/ only); run: cp THIRD_PARTY_LICENSES.md docker/"
    )


def test_every_package_in_the_lock_has_a_hash() -> None:
    text = (REPO_ROOT / "docker" / "requirements.lock").read_text()
    entries = re.findall(
        r"^[A-Za-z0-9_.\-]+==\S+ \\\n(?:\s+--hash=sha256:[0-9a-f]{64}.*\n)+", text, re.M
    )
    pins = re.findall(r"^[A-Za-z0-9_.\-]+==", text, re.M)
    assert pins and len(entries) == len(pins)


def test_lock_pins_match_the_plan() -> None:
    text = (REPO_ROOT / "docker" / "requirements.lock").read_text().lower()
    for pin in (
        "cocotb==2.1.0",
        "pytest==8.4.2",
        "amaranth==0.5.10",
        "amaranth-yosys==",
        "myhdl==0.11.52",
        "pywellen==0.25.7",
        "yowasp-yosys==0.62.0.0.post1088",
        "pyyaml==",
    ):
        assert pin in text


def test_dockerfile_pins_bases_by_digest_and_has_labels() -> None:
    text = (REPO_ROOT / "docker" / "Dockerfile").read_text()
    assert re.search(r"verilator/verilator:v5\.046@sha256:[0-9a-f]{64}", text)
    assert re.search(r"ubuntu:24\.04@sha256:[0-9a-f]{64}", text)
    assert "iverilog=12.0-2build2" in text
    assert "--require-hashes" in text
    assert "org.opencontainers.image.source" in text
    assert "--snapshot" in text


def test_everything_under_docker_is_tracked() -> None:
    out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "docker"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out == "", f"untracked files under docker/ are not part of the image tag: {out}"
