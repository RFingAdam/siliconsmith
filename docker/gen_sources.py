# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
"""Write the source and license information that ships inside the toolchain image.

Run once at image build time, with the image's own Python:

    python gen_sources.py <doc dir> <site-packages dir>

It writes `<doc dir>/SOURCES.txt` and copies the license files that the installed Python
packages carry into `<doc dir>/licenses/python/`. The Verilator and base image pins come in
through the environment so the Dockerfile stays the only place that states them.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

WASM_COMPONENTS = """\
Components compiled to WebAssembly inside the Python wheels (not separate programs):
  yowasp-yosys wheel: Yosys 0.62 (commit 7326bb7d6641500ecb285c291a54a662cb1e76cf),
    https://github.com/YosysHQ/yosys
    ABC (commit 734f64d5b907158dc4337ee82b3b74566d74ba08), https://github.com/YosysHQ/abc
    yosys-slang (commit 05dc50ce6728f020835273203b8d2838bf45254e),
    https://github.com/povik/yosys-slang
    slang (commit d7888c90a048e47384e530fef9863e65952c9e3c),
    https://github.com/MikePopoloski/slang
    SymbiYosys scripts (commit 16171682e419dac18037398a05b40c651aac3519),
    https://github.com/YosysHQ/SymbiYosys
    Packaging: https://github.com/YoWASP/yosys (branch release-0.62, commit c4048c8d)
  amaranth-yosys wheel: Yosys 0.50 (commit b5170e1394f602c607e75bdbb1a2b637118f2086),
    https://github.com/YosysHQ/yosys ; packaging: https://github.com/amaranth-lang/amaranth-yosys
  pywellen wheel: wellen and fst-reader (Rust), plus the crates listed in
    licenses/python/pywellen/pywellen.cyclonedx.json ; https://github.com/ekiwi/wellen
"""


def run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def apt_section() -> list[str]:
    fmt = "${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"
    rows = sorted(line.split("\t") for line in run("dpkg-query", "-W", "-f", fmt).splitlines())
    lines = [
        "Ubuntu packages: binary package, version, source package, source version.",
        "Get the source with `apt-get source <source>=<source version>` (deb-src enabled) or from",
        "https://launchpad.net/ubuntu/+source/<source>/<source version>.",
        f"Installed from the Ubuntu archive snapshot {os.environ.get('APT_SNAPSHOT', '?')}.",
        "",
    ]
    for binary, version, source, source_version in rows:
        lines.append(f"  {binary} {version}  <-  {source} {source_version}")
    return lines


def pip_section() -> list[str]:
    lines = ["Python packages in /opt/venv (hash-locked in docker/requirements.lock):", ""]
    for dist in sorted(
        importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        name, version = dist.metadata["Name"], dist.version
        urls = {}
        for entry in dist.metadata.get_all("Project-URL") or []:
            label, _, url = entry.partition(",")
            urls[label.strip().lower()] = url.strip()
        upstream = (
            urls.get("source")
            or urls.get("source code")
            or urls.get("repository")
            or urls.get("homepage")
            or dist.metadata.get("Home-page")
            or "-"
        )
        pypi = f"https://pypi.org/project/{name}/{version}/"
        lines.append(f"  {name} {version}  PyPI: {pypi}  upstream: {upstream}")
    return lines


def verilator_section() -> list[str]:
    return [
        "Verilator 5.046 (copied unmodified from the official Docker image):",
        "  image: " + os.environ.get("VERILATOR_IMAGE", "?"),
        "  source: https://github.com/verilator/verilator/archive/refs/tags/v5.046.tar.gz",
        "  tag: https://github.com/verilator/verilator/tree/v5.046",
        "  Distributed here under its Artistic-2.0 option.",
    ]


def copy_python_licenses(site: Path, target: Path) -> None:
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"].lower().replace("_", "-")
        files = [
            f
            for f in (dist.files or [])
            if "dist-info" in f.parts[0]
            and (
                any(key in f.name.lower() for key in ("licen", "copying", "notice"))
                or "sboms" in f.parts
            )
        ]
        for rel in files:
            source = Path(dist.locate_file(rel))
            if not source.is_file():
                continue
            dest = target / name / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)


def main() -> int:
    doc = Path(sys.argv[1])
    site = Path(sys.argv[2])
    doc.mkdir(parents=True, exist_ok=True)
    blocks = [
        ["Siliconsmith toolchain image: where the source of everything in it comes from.", ""],
        ["Base image: " + os.environ.get("UBUNTU_IMAGE", "?"), ""],
        apt_section(),
        [""],
        verilator_section(),
        [""],
        pip_section(),
        [""],
        WASM_COMPONENTS.splitlines(),
    ]
    (doc / "SOURCES.txt").write_text(
        "\n".join(line for block in blocks for line in block) + "\n", encoding="utf-8"
    )
    copy_python_licenses(site, doc / "licenses" / "python")
    return 0


if __name__ == "__main__":
    sys.exit(main())
