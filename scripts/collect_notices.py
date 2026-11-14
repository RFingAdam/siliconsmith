# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Collect third-party license notices for code that is compiled into binaries in the image.

The toolchain image redistributes compiled binaries (the pywellen and wasmtime Python wheels, and
the two WebAssembly Yosys builds) that contain MIT, BSD, ISC, Unicode-3.0, Zlib and Apache-2.0
licensed code from other projects. Those licenses want the copyright line and the permission
notice to travel with the binary, so this script gathers them into two generated files:

    docker/licenses/THIRD_PARTY_NOTICES-rust-crates.txt
    docker/licenses/THIRD_PARTY_NOTICES-yosys-bundled.txt
    docker/licenses/THIRD_PARTY_NOTICES-wasm-runtime.txt

It needs network access and is run by hand, never by CI. The output is deterministic (sorted,
no timestamps), so running it again gives byte-identical files; commit the result.

    python3 scripts/collect_notices.py resolve   # rebuild scripts/third_party_crates.json
    python3 scripts/collect_notices.py rust      # write the Rust crate notices
    python3 scripts/collect_notices.py yosys     # write the Yosys bundled-library notices
    python3 scripts/collect_notices.py wasm      # write the wasi-libc and LLVM runtime notices
    python3 scripts/collect_notices.py all       # rust + yosys (the default)

`resolve` needs pip, git and cargo. The Rust crate list is:
  * pywellen: the components of the CycloneDX SBOM inside the pinned wheel;
  * wasmtime: the crates.io packages that `cargo metadata` lists for the wasmtime C API at the
    pinned release tag (all features, normal dependencies, Linux x86-64). That is a superset of
    what is linked: it also holds crates that only run at build time (proc macros).
Crates that live in the wasmtime repository itself are covered by the wasmtime wheel's own
LICENSE file and are not listed. Each crate's files come from its immutable crates.io tarball.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATES_JSON = ROOT / "scripts" / "third_party_crates.json"
OUT_DIR = ROOT / "docker" / "licenses"
RUST_OUT = OUT_DIR / "THIRD_PARTY_NOTICES-rust-crates.txt"
YOSYS_OUT = OUT_DIR / "THIRD_PARTY_NOTICES-yosys-bundled.txt"
WASM_OUT = OUT_DIR / "THIRD_PARTY_NOTICES-wasm-runtime.txt"

PYWELLEN_VERSION = "0.25.7"
WASMTIME_TAG = "v47.0.1"
WASMTIME_REPO = "https://github.com/bytecodealliance/wasmtime.git"
WASMTIME_TARGET = "x86_64-unknown-linux-gnu"

LICENSE_NAME = re.compile(r"^(licen[cs]e|copying|notice|copyright|unlicense)", re.IGNORECASE)
RULE = "=" * 78
THIN = "-" * 78
# Texts at least this long (the full Apache-2.0, MPL, GPL texts) are printed once and referenced.
# Short MIT/BSD/ISC notices carry copyright lines and are always printed in full.
COMMON_MIN_BYTES = 8000


def fetch(url: str, retries: int = 4) -> bytes | None:
    """GET a URL. None on HTTP 404; other errors are retried and then raised."""
    request = urllib.request.Request(url, headers={"User-Agent": "siliconsmith-collect-notices"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data: bytes = response.read()
                return data
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 * (attempt + 1))
    return None


def text_of(data: bytes) -> str:
    """Decode a notice file to text with LF line ends and no trailing blank space."""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    text = text.removeprefix("﻿")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


# --- Rust crates ------------------------------------------------------------------------------


@dataclass
class Crate:
    name: str
    version: str
    spdx: str
    used_by: set[str]


def resolve_pywellen(work: Path) -> list[tuple[str, str, str]]:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--only-binary=:all:",
            "--platform=manylinux2014_x86_64",
            "--python-version=3.12",
            f"pywellen=={PYWELLEN_VERSION}",
            "-d",
            str(work),
        ],
        check=True,
        capture_output=True,
    )
    (wheel,) = work.glob("pywellen-*.whl")
    with zipfile.ZipFile(wheel) as zf:
        (member,) = [n for n in zf.namelist() if n.endswith("pywellen.cyclonedx.json")]
        sbom = json.loads(zf.read(member))
    out = []
    for comp in sbom["components"]:
        licenses = comp.get("licenses") or [{}]
        out.append((comp["name"], comp["version"], licenses[0].get("expression", "")))
    return out


def resolve_wasmtime(work: Path) -> list[tuple[str, str, str]]:
    src = work / "wasmtime"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--branch", WASMTIME_TAG, WASMTIME_REPO, str(src)],
        check=True,
    )
    meta = json.loads(
        subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version=1",
                "--locked",
                "--all-features",
                f"--filter-platform={WASMTIME_TARGET}",
                "--manifest-path",
                str(src / "crates" / "c-api" / "Cargo.toml"),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    (root,) = [i for i, p in packages.items() if p["name"] == "wasmtime-c-api-impl"]
    seen: set[str] = set()
    todo = [root]
    while todo:
        pkg = todo.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        for dep in nodes[pkg]["deps"]:
            if any(kind["kind"] is None for kind in dep["dep_kinds"]):
                todo.append(dep["pkg"])
    return [
        (packages[i]["name"], packages[i]["version"], packages[i]["license"] or "")
        for i in seen
        if (packages[i]["source"] or "").startswith("registry+")
    ]


def resolve() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sources = {
            "pywellen": resolve_pywellen(work),
            "wasmtime": resolve_wasmtime(work),
        }
    crates: dict[tuple[str, str], dict[str, object]] = {}
    for source, rows in sources.items():
        for name, version, spdx in rows:
            entry = crates.setdefault(
                (name, version), {"name": name, "version": version, "spdx": spdx, "used_by": []}
            )
            used_by = entry["used_by"]
            assert isinstance(used_by, list)
            used_by.append(source)
    data = {
        "pywellen": PYWELLEN_VERSION,
        "wasmtime": WASMTIME_TAG,
        "crates": [crates[key] for key in sorted(crates)],
    }
    CRATES_JSON.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    counts = {s: len(rows) for s, rows in sources.items()}
    print(f"wrote {CRATES_JSON.relative_to(ROOT)}: {len(crates)} unique crates {counts}")


def github_license_files(owner: str, repo: str, sha: str, directory: str) -> list[tuple[str, str]]:
    """License-like files in one directory of a GitHub repository at an exact commit."""
    listing = fetch(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{directory}?ref={sha}".replace(
            "contents/?", "contents?"
        )
    )
    if listing is None:
        return []
    found = []
    for entry in json.loads(listing):
        if entry["type"] == "file" and (
            LICENSE_NAME.match(entry["name"]) or directory.endswith("LICENSES")
        ):
            data = fetch(entry["download_url"])
            if data is not None:
                found.append((entry["name"], text_of(data)))
        elif (
            entry["type"] == "dir"
            and entry["name"] == "LICENSES"
            and not directory.endswith("LICENSES")
        ):
            found += [
                (f"LICENSES/{name}", text)
                for name, text in github_license_files(
                    owner, repo, sha, f"{directory}/LICENSES".lstrip("/")
                )
            ]
    return sorted(found)


def is_vendored_c_license(crate: str, rel: str) -> bool:
    """License files of C libraries vendored inside a *-sys crate (not tests or bindings)."""
    parts = rel.split("/")
    skipped = {"test", "tests", "bindings", "doc", "docs", "examples", "contrib", "programs"}
    skipped |= {"suite", "regress"}
    return (
        crate.endswith("-sys")
        and 1 < len(parts) <= 4
        and LICENSE_NAME.match(parts[-1]) is not None
        and not skipped.intersection(p.lower() for p in parts[:-1])
    )


def crate_license_files(name: str, version: str) -> tuple[list[tuple[str, str]], str] | None:
    """License-like files of a crate and where they came from. None if the download is missing.

    The crates.io tarball comes first: files at its top level and under a top-level LICENSES
    folder. If it has none, the crate's GitHub repository at the commit recorded in the tarball
    (.cargo_vcs_info.json) is searched, in the crate's folder and then the repository root.
    """
    data = fetch(f"https://static.crates.io/crates/{name}/{name}-{version}.crate")
    if data is None:
        return None
    prefix = f"{name}-{version}/"
    found: list[tuple[str, str]] = []
    vcs: dict[str, object] = {}
    manifest = ""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            rel = member.name.removeprefix(prefix)
            extracted = tar.extractfile(member) if member.isfile() else None
            if extracted is None:
                continue
            if rel == ".cargo_vcs_info.json":
                vcs = json.loads(extracted.read())
            elif rel == "Cargo.toml":
                manifest = extracted.read().decode("utf-8", errors="replace")
            elif (
                ("/" not in rel and LICENSE_NAME.match(rel))
                or (rel.startswith("LICENSES/") and rel.count("/") == 1)
                or is_vendored_c_license(name, rel)
            ):
                found.append((rel, text_of(extracted.read())))
    if found:
        return sorted(found), "the crates.io package"
    repo = re.search(
        r'^repository = "https://github.com/([^/"]+)/([^/"]+?)/?(?:tree/[^/"]+/([^"]*))?"',
        manifest,
        re.MULTILINE,
    )
    git = vcs.get("git")
    if repo is None or not isinstance(git, dict):
        return [], ""
    owner, repo_name, tree_dir = repo.group(1), repo.group(2), repo.group(3) or ""
    sha = str(git["sha1"])
    for folder in dict.fromkeys([str(vcs.get("path_in_vcs") or tree_dir), ""]):
        files = github_license_files(owner, repo_name, sha, folder)
        if files:
            where = f"https://github.com/{owner}/{repo_name} at commit {sha}"
            return files, where + (f", folder {folder}" if folder else "")
    return [], ""


def write_rust_notices() -> None:
    data = json.loads(CRATES_JSON.read_text(encoding="utf-8"))
    crates = [Crate(c["name"], c["version"], c["spdx"], set(c["used_by"])) for c in data["crates"]]
    commons: dict[str, tuple[str, str]] = {}  # sha256 -> (id, text)
    blocks: list[str] = []
    no_file: list[str] = []
    missing: list[str] = []
    for crate in sorted(crates, key=lambda c: (c.name, c.version)):
        found = crate_license_files(crate.name, crate.version)
        files, origin = found if found is not None else (None, "")
        head = [
            RULE,
            f"crate: {crate.name} {crate.version}",
            f"license (SPDX expression from the crate's metadata): {crate.spdx or 'not stated'}",
            f"source: https://crates.io/crates/{crate.name}/{crate.version}",
            "compiled into: " + ", ".join(sorted(crate.used_by)),
            THIN,
        ]
        body: list[str] = []
        if origin and origin != "the crates.io package":
            body.append(
                "The crates.io package has no license file. These files are from "
                f"{origin}, the commit the package was built from."
            )
        if files is None:
            missing.append(f"{crate.name} {crate.version}")
            body.append("The crates.io tarball for this crate could not be downloaded.")
        elif not files:
            no_file.append(f"{crate.name} {crate.version}")
            body.append(
                "This crate ships no license file in its published package, and neither does "
                "its repository at the commit it was built from. Its Cargo.toml states the SPDX "
                "expression above. If you take it under Apache-2.0, the license text is "
                "{APACHE}."
            )
        for rel, text in files or []:
            digest = hashlib.sha256(text.encode()).hexdigest()
            if len(text.encode()) >= COMMON_MIN_BYTES:
                ident = commons.setdefault(digest, (f"COMMON-TEXT-{len(commons) + 1}", text))[0]
                body.append(f"[{rel}] identical to {ident}, printed once at the end of this file.")
            else:
                body.append(f"[{rel}]\n{text}")
        blocks.append("\n".join(head) + "\n" + "\n\n".join(body) + "\n")
    intro = [
        "Third-party notices for the Rust crates compiled into binaries in this image.",
        "",
        f"pywellen {data['pywellen']}: the crates listed in the SBOM inside the wheel",
        f"(pywellen-{data['pywellen']}.dist-info/sboms/pywellen.cyclonedx.json).",
        f"wasmtime (wasmtime-py wheel, built from Wasmtime {data['wasmtime']}): the crates.io",
        f"dependencies of the Wasmtime C API at tag {data['wasmtime']}, a superset of what is",
        "linked (it also lists crates that only run at build time). Crates that live in the",
        "Wasmtime repository itself are covered by the wasmtime wheel's LICENSE file",
        "(Apache-2.0 WITH LLVM-exception), kept in licenses/python/wasmtime/.",
        "",
        f"{len(crates)} crates, each followed by the license files from its crates.io package",
        "(https://static.crates.io/crates/<name>/<name>-<version>.crate), verbatim. Very long",
        "texts that are identical between crates (for example the Apache-2.0 text) are printed",
        "once at the end. Where a crate offers a choice (for example MIT OR Apache-2.0) you may",
        "use it under any of the offered licenses.",
        "",
        "Generated by scripts/collect_notices.py. Do not edit by hand.",
        "",
    ]
    tail = [
        RULE,
        "Texts printed once",
        RULE,
    ]
    for ident, text in sorted(commons.values()):
        tail += ["", THIN, ident, THIN, text]
    if no_file:
        intro.insert(-2, "Crates without a license file in their package: " + ", ".join(no_file))
        intro.insert(-2, "")
    out = "\n".join(intro) + "\n" + "\n".join(blocks) + "\n" + "\n".join(tail) + "\n"
    apache_ids = [i for i, t in commons.values() if "Apache License" in t and "Version 2.0" in t]
    out = out.replace("{APACHE}", f"{apache_ids[0]} at the end of this file" if apache_ids else "?")
    RUST_OUT.write_text(out, encoding="utf-8")
    print(f"wrote {RUST_OUT.relative_to(ROOT)}: {len(crates)} crates, {len(commons)} common texts")
    print(f"no license file in tarball ({len(no_file)}): {no_file}")
    print(f"download failed ({len(missing)}): {missing}")
    if missing:
        sys.exit(1)


# --- Libraries bundled in the Yosys source trees ---------------------------------------------


@dataclass
class Item:
    """One notice. `where` is the pinned URL base; `path` the file; `mode` how to cut the text."""

    component: str
    spdx: str
    note: str
    base: str
    path: str
    mode: str  # "file", "header" (leading comment block) or "legal" (README 'Legal' section)


YOSYS_TAGS = {"v0.62": "yowasp-yosys (Yosys 0.62)", "v0.50": "amaranth-yosys (Yosys 0.50)"}
RAW_YOSYS = "https://raw.githubusercontent.com/YosysHQ/yosys"
CXXOPTS_COMMIT = "4bf61f08697b110d9e3991864650a405b3dd515d"  # libs/cxxopts submodule, both tags

SLANG_COMMIT = "d7888c90a048e47384e530fef9863e65952c9e3c"
FMT_COMMIT = "553ec11ec06fbe0beebfbb45f9dc3c9eabd83d28"  # yosys-slang third_party/fmt submodule
RAW_SLANG = f"https://raw.githubusercontent.com/MikePopoloski/slang/{SLANG_COMMIT}"


def yosys_items(tag: str) -> list[Item]:
    base = f"{RAW_YOSYS}/{tag}"
    return [
        Item("MiniSat", "MIT", "libs/minisat", base, "libs/minisat/LICENSE", "file"),
        Item(
            "cxxopts",
            "MIT",
            "libs/cxxopts, a git submodule of jarro2783/cxxopts at " + CXXOPTS_COMMIT[:8],
            f"https://raw.githubusercontent.com/jarro2783/cxxopts/{CXXOPTS_COMMIT}",
            "LICENSE",
            "file",
        ),
        Item("ezSAT", "ISC", "libs/ezsat", base, "libs/ezsat/ezsat.h", "header"),
        Item(
            "SubCircuit", "ISC", "libs/subcircuit", base, "libs/subcircuit/subcircuit.h", "header"
        ),
        Item("json11", "MIT", "libs/json11", base, "libs/json11/json11.cpp", "header"),
        Item(
            "SHA-1 in C++",
            "LicenseRef-PublicDomain",
            "libs/sha1",
            base,
            "libs/sha1/sha1.h",
            "header",
        ),
        Item(
            "BigInt (Matt McCutchen)",
            "LicenseRef-PublicDomain",
            "libs/bigint",
            base,
            "libs/bigint/README",
            "legal",
        ),
        Item(
            "FST writer API (GTKWave)",
            "MIT",
            "libs/fst/fstapi.cc",
            base,
            "libs/fst/fstapi.cc",
            "header",
        ),
        Item("LZ4", "BSD-2-Clause", "libs/fst/lz4.cc", base, "libs/fst/lz4.cc", "header"),
        Item("FastLZ", "MIT", "libs/fst/fastlz.cc", base, "libs/fst/fastlz.cc", "header"),
    ]


def slang_items() -> list[Item]:
    thread_pool = "https://raw.githubusercontent.com/bshoshany/thread-pool/v5.0.0"
    expected = "https://raw.githubusercontent.com/martinmoene/expected-lite/v0.9.0"
    return [
        Item(
            "fmt",
            "MIT",
            "yosys-slang third_party/fmt, a git submodule of fmtlib/fmt at " + FMT_COMMIT[:8],
            f"https://raw.githubusercontent.com/fmtlib/fmt/{FMT_COMMIT}",
            "LICENSE",
            "file",
        ),
        Item(
            "BS::thread_pool",
            "MIT",
            "slang external/BS_thread_pool.hpp, upstream v5.0.0",
            thread_pool,
            "LICENSE.txt",
            "file",
        ),
        Item(
            "expected-lite",
            "BSL-1.0",
            "slang external/expected.hpp, upstream v0.9.0",
            expected,
            "LICENSE.txt",
            "file",
        ),
        Item(
            "Boost.Unordered (single header)",
            "BSL-1.0",
            "slang external/boost_unordered.hpp; copyright lines from the file header, license "
            "text from slang's LICENSES/BSL-1.0.txt",
            RAW_SLANG,
            "external/boost_unordered.hpp",
            "header",
        ),
        Item(
            "Boost Software License 1.0 (as shipped by slang)",
            "BSL-1.0",
            "slang LICENSES/BSL-1.0.txt",
            RAW_SLANG,
            "LICENSES/BSL-1.0.txt",
            "file",
        ),
    ]


def leading_comment(text: str) -> str:
    """The first comment block of a source file, verbatim, without the comment markers' noise."""
    lines = text.split("\n")
    out: list[str] = []
    if lines and lines[0].lstrip().startswith("//"):
        for line in lines:
            if not line.lstrip().startswith("//"):
                break
            out.append(line)
        return "\n".join(out)
    in_block = False
    for line in lines:
        if not in_block and line.strip() == "":
            continue
        if not in_block and not line.lstrip().startswith("/*"):
            break
        in_block = True
        out.append(line)
        if "*/" in line:
            break
    return "\n".join(out)


def legal_section(text: str) -> str:
    """README lines from the 'Legal' heading up to the next '~~~~' separator."""
    lines = text.split("\n")
    start = lines.index("Legal")
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("~~~~"))
    return "\n".join(lines[start:end]).strip("\n")


def notice_text(item: Item) -> str:
    data = fetch(f"{item.base}/{item.path}")
    if data is None:
        raise SystemExit(f"not found: {item.base}/{item.path}")
    text = text_of(data)
    if item.mode == "header":
        text = text_of(leading_comment(text).encode())
    elif item.mode == "legal":
        text = legal_section(text)
    return text


def render_items(pairs: list[tuple[Item, str]]) -> tuple[list[str], int]:
    """Notice blocks for (item, build) pairs. Identical texts of one component print once."""
    # component -> (spdx, note, text) -> [(build, source URL)] that carry exactly that text
    grouped: dict[str, dict[tuple[str, str, str], list[tuple[str, str]]]] = {}
    order: list[str] = []
    for item, build in pairs:
        if item.component not in order:
            order.append(item.component)
        key = (item.spdx, item.note, notice_text(item))
        grouped.setdefault(item.component, {}).setdefault(key, []).append(
            (build, f"{item.base}/{item.path}")
        )
    blocks = []
    for component in order:
        for (spdx, note, text), sources in sorted(grouped[component].items()):
            blocks.append(
                "\n".join(
                    [
                        RULE,
                        f"component: {component}",
                        f"license (SPDX): {spdx}",
                        f"where: {note}",
                        "bundled in: " + "; ".join(sorted({b for b, _ in sources})),
                        *(f"file: {url}" for _, url in sorted(sources)),
                        THIN,
                        text,
                        "",
                    ]
                )
            )
    return blocks, len(order)


def write_yosys_notices() -> None:
    pairs = [(item, build) for tag, build in YOSYS_TAGS.items() for item in yosys_items(tag)]
    pairs += [(item, "yowasp-yosys (yosys-slang and slang)") for item in slang_items()]
    blocks, count = render_items(pairs)
    intro = [
        "Third-party notices for libraries bundled in the Yosys source trees that are compiled",
        "into the WebAssembly Yosys builds of this image.",
        "",
        "yowasp-yosys contains Yosys 0.62 (tag v0.62, commit 7326bb7d), yosys-slang and slang.",
        "amaranth-yosys contains Yosys 0.50 (tag v0.50, commit b5170e13), built without ABC and",
        "without plugins. Notices come from the YosysHQ/yosys repository at those exact tags.",
        "A notice that is identical in both trees is printed once and lists both builds.",
        "Yosys itself (ISC), ABC, yosys-slang and slang have their own files in this folder.",
        "libs/dlfcn-win32 (Windows only) is not part of these Linux/WebAssembly builds.",
        "",
        "Generated by scripts/collect_notices.py. Do not edit by hand.",
        "",
    ]
    YOSYS_OUT.write_text("\n".join(intro) + "\n" + "\n".join(blocks), encoding="utf-8")
    print(f"wrote {YOSYS_OUT.relative_to(ROOT)}: {count} components")


# --- C and C++ runtime libraries inside the WebAssembly Yosys builds -------------------------

# Both builds are compiled with a wasi-sdk release (see each build.sh). The release tag pins the
# wasi-libc and llvm-project submodule commits (src/wasi-libc, src/llvm-project).
WASI_BUILDS = {
    "yowasp-yosys 0.62 (wasi-sdk-27)": (
        "wasi-sdk-27",
        "3f7eb4c7d6ede4dde3c4bffa6ed14e8d656fe93f",
        "87f0227cb60147a26a1eeb4fb06e3b505e9c7261",
    ),
    "amaranth-yosys 0.50 (wasi-sdk-25)": (
        "wasi-sdk-25",
        "574b88da481569b65a237cb80daf9a2d5aeaf82d",
        "ab4b5a2db582958af1ee308a790cfdb42bd24720",
    ),
}
LLVM_LICENSE_FILES = {
    "LLVM Project": "LICENSE.TXT",
    "libc++": "libcxx/LICENSE.TXT",
    "libc++abi": "libcxxabi/LICENSE.TXT",
    "compiler-rt": "compiler-rt/LICENSE.TXT",
}


def wasi_libc_items(commit: str) -> list[Item]:
    listing = fetch(
        f"https://api.github.com/repos/WebAssembly/wasi-libc/git/trees/{commit}?recursive=1"
    )
    assert listing is not None
    base = f"https://raw.githubusercontent.com/WebAssembly/wasi-libc/{commit}"
    paths = sorted(
        e["path"]
        for e in json.loads(listing)["tree"]
        if e["type"] == "blob" and LICENSE_NAME.match(e["path"].rsplit("/", 1)[-1])
    )
    return [
        Item(
            f"wasi-libc: {path}",
            "see file (wasi-libc mixes Apache-2.0 WITH LLVM-exception, MIT, BSD and musl code)",
            "wasi-libc (commits in the header of this file)",
            base,
            path,
            "file",
        )
        for path in paths
    ]


def llvm_items(commit: str) -> list[Item]:
    base = f"https://raw.githubusercontent.com/llvm/llvm-project/{commit}"
    return [
        Item(
            f"{name} license file",
            "Apache-2.0 WITH LLVM-exception (plus the permissive notices listed in the file)",
            f"llvm-project, {path} (commits in the header of this file)",
            base,
            path,
            "file",
        )
        for name, path in LLVM_LICENSE_FILES.items()
    ]


def write_wasm_runtime_notices() -> None:
    pairs: list[tuple[Item, str]] = []
    for build, (_, libc, llvm) in WASI_BUILDS.items():
        pairs += [(item, build) for item in wasi_libc_items(libc) + llvm_items(llvm)]
    blocks, count = render_items(pairs)
    intro = [
        "Third-party notices for the C and C++ runtime libraries compiled into the WebAssembly",
        "Yosys builds (the yowasp-yosys and amaranth-yosys wheels).",
        "",
        "Both builds are compiled with a wasi-sdk release, which supplies the C library and the",
        "C++ runtime that end up in the .wasm files (build.sh of each project):",
    ]
    for build, (tag, libc, llvm) in WASI_BUILDS.items():
        intro += [
            f"  {build}: https://github.com/WebAssembly/wasi-sdk/tree/{tag}",
            f"    wasi-libc commit {libc[:12]}, llvm-project commit {llvm[:12]}",
            "    (the submodule commits recorded in that wasi-sdk tag; libc++, libc++abi and",
            "    compiler-rt come from that llvm-project commit)",
        ]
    intro += [
        "",
        "The license files below are taken from those exact commits. wasi-libc contains musl-",
        "derived code (MIT, see libc-top-half/musl/COPYRIGHT), cloudlibc (BSD-2-Clause), and",
        "WASI headers and glue under Apache-2.0 WITH LLVM-exception. dlmalloc in wasi-libc is",
        "public domain and has no license file. libc++, libc++abi and compiler-rt are under",
        "Apache-2.0 WITH LLVM-exception.",
        "",
        "The LLVM exception waives the attribution conditions of Apache-2.0 for code that is",
        "embedded in object code, so the LLVM-licensed texts are shipped here for completeness",
        "and not because the exception requires it. The MIT and BSD notices (musl, cloudlibc,",
        "musl-fts) do need to travel with the binaries.",
        "Yosys's own bundled libraries are in THIRD_PARTY_NOTICES-yosys-bundled.txt.",
        "",
        "The wasmtime shared library (libwasmtime.so in the wasmtime wheel) is different: it",
        "is Rust code plus the C code of the zstd-sys crate (zstd 1.5.5; its BSD-3-Clause",
        "license file is in THIRD_PARTY_NOTICES-rust-crates.txt, under zstd-sys), and it",
        "dynamically links only the system libraries libc, libm, libdl, libpthread and",
        "libgcc_s (they come from the Ubuntu base image). No other C or C++ library is",
        "linked into it: no libc++, no capstone, no Intel ITT code (checked on the wheel's",
        "ELF NEEDED entries and symbol strings).",
        "",
        "Generated by scripts/collect_notices.py. Do not edit by hand.",
        "",
    ]
    WASM_OUT.write_text("\n".join(intro) + "\n" + "\n".join(blocks), encoding="utf-8")
    print(f"wrote {WASM_OUT.relative_to(ROOT)}: {count} components")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument(
        "command", nargs="?", default="all", choices=["resolve", "rust", "yosys", "wasm", "all"]
    )
    args = parser.parse_args()
    if args.command == "resolve":
        resolve()
    if args.command in ("rust", "all"):
        write_rust_notices()
    if args.command in ("yosys", "all"):
        write_yosys_notices()
    if args.command in ("wasm", "all"):
        write_wasm_runtime_notices()
    return 0


if __name__ == "__main__":
    sys.exit(main())
