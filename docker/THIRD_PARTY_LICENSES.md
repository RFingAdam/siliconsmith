# Third-party licenses

Siliconsmith itself is Apache-2.0. The tools below are **not** part of this repository's source. They are invoked as external processes (or, where the license allows and the plan says so, used as unmodified Python dependencies). None of them may be vendored into this repo. Our toolchain image (published from M0-2) redistributes some of them unmodified; the package tables below and rule 3 cover that.

Licenses were checked on **2026-11-11 (CT)** against each project's official license file or copyright notice, as linked. Items marked *(unverified)* still need a check. This is not legal advice.

| Tool | Official source | License (as verified) | How we use it | Notes |
|---|---|---|---|---|
| [Amaranth](https://github.com/amaranth-lang/amaranth) | [LICENSE.txt](https://github.com/amaranth-lang/amaranth/blob/main/LICENSE.txt) | BSD-2-Clause | Optional Python dependency (`import`) | Permissive |
| [MyHDL](https://github.com/myhdl/myhdl) | [LICENSE.txt](https://github.com/myhdl/myhdl/blob/master/LICENSE.txt) and source headers (e.g. [`myhdl/_block.py`](https://github.com/myhdl/myhdl/blob/master/myhdl/_block.py)) | LGPL-2.1 (headers: version 2.1 or any later version) | Optional pip dependency (`import`), unmodified, not vendored; in the toolchain image | LGPL allows use from Apache-2.0 code. If we patch it, publish the patch under LGPL. |
| [cocotb](https://github.com/cocotb/cocotb) | [LICENSE](https://github.com/cocotb/cocotb/blob/master/LICENSE) | BSD-3-Clause | Python dependency | Permissive |
| [Yosys](https://github.com/YosysHQ/yosys) | [COPYING](https://github.com/YosysHQ/yosys/blob/main/COPYING) | ISC | External process | Permissive. In M0 the toolchain image runs Yosys 0.62 compiled to WebAssembly (`yowasp-yosys`, table below). |
| [Icarus Verilog](https://github.com/steveicarus/iverilog) | [COPYING](https://github.com/steveicarus/iverilog/blob/master/COPYING) + source headers (e.g. `main.cc`) | **GPL-2.0-or-later** | External process only | Headers say GPLv2 or later. Never link or vendor. Simulation output is ours. |
| [Verilator](https://github.com/verilator/verilator) | [README.rst](https://github.com/verilator/verilator/blob/master/README.rst) (`SPDX-License-Identifier: LGPL-3.0-only OR Artistic-2.0`) and [LICENSE](https://github.com/verilator/verilator/blob/master/LICENSE) | **LGPL-3.0-only OR Artistic-2.0** | External process | Generated C++ models are ours; Verilator runtime files stay under its license. The toolchain image copies the Verilator files from the official [`verilator/verilator:v5.046`](https://hub.docker.com/r/verilator/verilator) image, pinned by tag and digest (`sha256:8003e282…`), not from apt (Ubuntu 24.04's apt Verilator is 5.020, older than the 5.036 that cocotb 2.1 needs). We distribute it under the Artistic-2.0 option (rule 3). |
| [LibreLane](https://github.com/librelane/librelane) | [License](https://github.com/librelane/librelane/blob/main/License) | Apache-2.0 | Official container image `ghcr.io/librelane/librelane:3.0.14`, pinned by digest and run as an external process (M1 onward). Never a Python dependency of our package. | Successor to OpenLane 2. [chipfoundry/openlane2](https://github.com/chipfoundry/openlane2) is also Apache-2.0. Its pip package depends on the GPL `klayout` Python module, which is one reason we only use the container. We do not re-publish the image. |
| [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) | [LICENSE](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/LICENSE) | BSD-3-Clause (source); the `openroad` binary includes GPL-3.0 OpenSTA | Via LibreLane, external process only | The source is BSD-3-Clause, but OpenROAD builds OpenSTA into the `openroad` binary from its `src/sta` submodule ([.gitmodules](https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/.gitmodules)). Treat distributed `openroad` binaries as **GPL-3.0**. [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/LICENSE_BUILD_RUN_SCRIPTS) build/run scripts are BSD-3-Clause; bundled tools keep their own licenses. |
| [OpenSTA](https://github.com/The-OpenROAD-Project/OpenSTA) | [LICENSE](https://github.com/The-OpenROAD-Project/OpenSTA/blob/master/LICENSE), [README.md](https://github.com/The-OpenROAD-Project/OpenSTA/blob/master/README.md) and source headers (e.g. [`search/Sta.cc`](https://github.com/The-OpenROAD-Project/OpenSTA/blob/master/search/Sta.cc)) | **GPL-3.0** (headers: version 3 or any later version). The README says it is dual licensed: GPL v3, and a commercial license from Parallax Software. | Via LibreLane (inside `openroad`, and as the standalone `sta`), external process only | Treat distributed `openroad` and `sta` binaries as GPL-3.0. Never link or vendor. |
| [Magic](https://github.com/RTimothyEdwards/magic) | [LICENSE](https://github.com/RTimothyEdwards/magic/blob/master/LICENSE) | Permissive UC Berkeley-style notice | External process, also via LibreLane | Notice grants use/copy/modify/distribute without fee, with copyright retained. It also mentions that export of the software outside the United States may require an export license. |
| [KLayout](https://github.com/KLayout/klayout) | [COPYRIGHT](https://github.com/KLayout/klayout/blob/master/COPYRIGHT) and [LICENSE](https://github.com/KLayout/klayout/blob/master/LICENSE) | **GPL-3.0-or-later** | External process only (DRC, render) | COPYRIGHT says GPLv3 or later. Do **not** `import klayout` into our Apache-2.0 code; call the CLI. |
| [SkyWater sky130 PDK](https://github.com/google/skywater-pdk) | [LICENSE](https://github.com/google/skywater-pdk/blob/main/LICENSE) | Apache-2.0 | Downloaded at build time, not committed | |
| [open_pdks](https://github.com/fossi-foundation/open-pdks) | [LICENSE](https://github.com/fossi-foundation/open-pdks/blob/main/LICENSE) | Apache-2.0 | Via ciel | |
| [ciel](https://github.com/fossi-foundation/ciel) | [License](https://github.com/fossi-foundation/ciel/blob/main/License) | Apache-2.0 | PDK version manager (volare successor) | |
| [IHP Open PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) | [LICENSE](https://github.com/IHP-GmbH/IHP-Open-PDK/blob/main/LICENSE) | Apache-2.0 | Later digital PDK work and RF experiments | SiGe BiCMOS, the more promising open PDK for RF. |
| [GF180MCU PDK](https://github.com/google/gf180mcu-pdk) | [LICENSE](https://github.com/google/gf180mcu-pdk/blob/main/LICENSE) | Apache-2.0 | Later digital PDK work | Google repo is archived; current builds come via open_pdks/ciel *(verify in the milestone that uses it)*. |
| Tiny Tapeout [verilog template](https://github.com/TinyTapeout/ttsky-verilog-template), [analog template](https://github.com/TinyTapeout/ttsky-analog-template), [tt-support-tools](https://github.com/TinyTapeout/tt-support-tools), [tt-gds-action](https://github.com/TinyTapeout/tt-gds-action) | Each repo's [LICENSE](https://github.com/TinyTapeout/ttsky-verilog-template/blob/main/LICENSE) | Apache-2.0 | We generate repos in their format and keep their Apache headers when copying template files | Compatible with ours |
| [Xschem](https://github.com/StefanSchippers/xschem) | [LICENSE](https://github.com/StefanSchippers/xschem/blob/master/LICENSE) | **GPL-2.0-or-later** | Analog track, external process | Bundled `scconfig/` is LGPL-2-or-later. Never vendor. |
| [ngspice](https://github.com/ngspice/ngspice) | Official [COPYING](https://github.com/ngspice/ngspice/blob/master/COPYING) (also on [SourceForge](https://sourceforge.net/p/ngspice/ngspice/ci/master/tree/COPYING)) | **Mixed:** core is New BSD / modified BSD (SPICE 3f5); some files differ | Analog track, external process | COPYING states the project as a whole is not under one license. numparam, ADMS scripts, and tclspice are LGPL; CIDER is old BSD; XSPICE is public domain. We invoke the ngspice binary only. |
| [netgen](https://github.com/RTimothyEdwards/netgen) | Source headers (e.g. `base/actel.c`) and [Copying](https://github.com/RTimothyEdwards/netgen/blob/master/Copying) | **GPL, any version** | Analog/digital LVS, external process only | Headers say "GNU GPL as published by the FSF (any version)." The bundled `Copying` file is GPL-1. Never vendor. |
| [openEMS](https://github.com/thliebig/openEMS) | [COPYING](https://github.com/thliebig/openEMS/blob/master/COPYING) and [README.md](https://github.com/thliebig/openEMS/blob/master/README.md) | **GPL-3.0** | EM solver, external process only | README says "GNU General Public License v3" and does not add "or later." This is an electromagnetic FDTD solver, not a piezoelectric acoustic solver. |
| [Elmer FEM](https://github.com/ElmerCSC/elmerfem) | [LICENSE.md](https://github.com/ElmerCSC/elmerfem/blob/devel/LICENSE.md) ("License of Elmer") | **GPL-2.0-or-later** (ElmerGUI has an extra Open CASCADE linking exception; some libraries/modules are LGPL) | Acoustic track FEM, external process only | [ElmerLicensePolicy.md](https://github.com/ElmerCSC/elmerfem/blob/devel/license_texts/ElmerLicensePolicy.md) notes a mix of GPL and LGPL across modules. We call the Elmer binaries, we do not link them. Whether the piezoelectric solver meets our needs is *(unverified until S1)*. |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | [LICENSE](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) | MIT | Library | Permissive |

**Mason and BVD (Butterworth-Van Dyke)** are equivalent-circuit *models*, not third-party programs. Implementations we write for the acoustic track are Apache-2.0.

## Packages in the toolchain image

These will be redistributed, unmodified, inside `ghcr.io/rfingadam/siliconsmith-toolchain` (published from M0-2). Versions are what the planned pins resolved to on 2026-11-11; `docker/requirements.lock` and the image's `SOURCES.txt` are authoritative once they exist. Tools already in the table above (Icarus, Verilator, cocotb, Amaranth, MyHDL) are not repeated.

### Python packages (image venv `/opt/venv`)

| Package | Version | License (as verified) | Official source | Notes |
|---|---|---|---|---|
| yowasp-yosys | 0.62.0.0.post1088 | ISC for the packaging and for Yosys; the bundled components are listed in Notes | [LICENSE.txt](https://github.com/YoWASP/yosys/blob/develop/LICENSE.txt) (packaging) | The image's `yosys` command in M0. The wheel contains Yosys 0.62 (ISC, YosysHQ/yosys [COPYING](https://github.com/YosysHQ/yosys/blob/7326bb7d6641500ecb285c291a54a662cb1e76cf/COPYING) at `7326bb7d`), ABC (permissive UC Berkeley notice, YosysHQ's fork of Berkeley ABC, [copyright.txt](https://github.com/YosysHQ/abc/blob/734f64d5b907158dc4337ee82b3b74566d74ba08/copyright.txt) at `734f64d5`), yosys-slang (ISC, povik/yosys-slang [LICENSE](https://github.com/povik/yosys-slang/blob/05dc50ce6728f020835273203b8d2838bf45254e/LICENSE) at `05dc50ce`) and slang (MIT, MikePopoloski/slang [LICENSE](https://github.com/MikePopoloski/slang/blob/d7888c90a048e47384e530fef9863e65952c9e3c/LICENSE) at `d7888c90`), compiled to WebAssembly, plus the SymbiYosys scripts (ISC, [COPYING](https://github.com/YosysHQ/SymbiYosys/blob/16171682e419dac18037398a05b40c651aac3519/COPYING) at `16171682`). Yosys and yosys-slang also bundle small MIT-licensed libraries (for example cxxopts, MiniSat and fmt). YoWASP's LICENSE.txt (ISC) covers the packaging. Commits: the Yosys commit is printed by `yosys -V`; the others are the submodule commits on YoWASP/yosys branch `release-0.62` at `c4048c8d`, the 1088th commit, which matches the `post1088` build number. |
| yowasp-runtime | 1.96 | ISC | [LICENSE.txt](https://github.com/YoWASP/runtime-py/blob/develop/LICENSE.txt) | Pulled in by yowasp-yosys |
| amaranth-yosys | 0.50.0.0.post130 | ISC | [LICENSE.txt](https://github.com/amaranth-lang/amaranth-yosys/blob/develop/LICENSE.txt) (packaging); Yosys [COPYING](https://github.com/YosysHQ/yosys/blob/b5170e1394f602c607e75bdbb1a2b637118f2086/COPYING) (the binary) | Pulled in by `amaranth[builtin-yosys]`. Yosys 0.50 (commit `b5170e13`) compiled to WebAssembly with only the features Amaranth needs; built without ABC and without plugins (`ENABLE_ABC := 0`, `ENABLE_PLUGINS := 0` in its `build.sh`). |
| wasmtime (wasmtime-py) | 47.0.1 | Apache-2.0 WITH LLVM-exception | [LICENSE](https://github.com/bytecodealliance/wasmtime-py/blob/main/LICENSE) (the LLVM exception is at the end of the file) | Runs the two WebAssembly Yosys builds |
| pywellen | 0.25.7 | BSD-3-Clause | [wellen LICENSE](https://github.com/ekiwi/wellen/blob/main/LICENSE) | Wave reader for `inspect_waves`; a compiled Rust extension that includes wellen and [fst-reader](https://github.com/ekiwi/fst-reader/blob/main/LICENSE) (both BSD-3-Clause). The wheel's embedded SBOM lists 51 Rust crates, all under permissive licenses (MIT, Apache-2.0, BSD-3-Clause, Zlib, Unlicense, 0BSD, Unicode-3.0, mostly as dual-license options). The image ships these license files. |
| pytest | 8.4.2 | MIT | [LICENSE](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | Required by cocotb 2.1; same pin as TT's template |
| pluggy | 1.7.0 | MIT | [LICENSE](https://github.com/pytest-dev/pluggy/blob/main/LICENSE) | Pulled in by pytest |
| iniconfig | 2.3.1 | MIT | [LICENSE](https://github.com/pytest-dev/iniconfig/blob/main/LICENSE) | Pulled in by pytest |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [LICENSE](https://github.com/pypa/packaging/blob/main/LICENSE), [PyPI](https://pypi.org/project/packaging/26.3/) | Pulled in by pytest |
| Pygments | 2.21.0 | BSD-2-Clause | [LICENSE](https://github.com/pygments/pygments/blob/master/LICENSE) | Pulled in by pytest |
| find_libpython | 0.5.1 | MIT | [LICENSE](https://github.com/ktbarrett/find_libpython/blob/master/LICENSE) | Pulled in by cocotb |
| Jinja2 | 3.1.6 | BSD-3-Clause | [LICENSE.txt](https://github.com/pallets/jinja/blob/main/LICENSE.txt) | Pulled in by Amaranth |
| MarkupSafe | 3.0.4 | BSD-3-Clause | [LICENSE.txt](https://github.com/pallets/markupsafe/blob/main/LICENSE.txt) | Pulled in by Jinja2 |
| jschon | 0.11.1 | MIT | [LICENSE](https://github.com/marksparkza/jschon/blob/main/LICENSE) | Pulled in by Amaranth |
| rfc3986 | 2.0.0 | Apache-2.0 | [LICENSE](https://github.com/python-hyper/rfc3986/blob/main/LICENSE), [PyPI](https://pypi.org/project/rfc3986/2.0.0/) | Pulled in by jschon |
| pyvcd | 0.4.2 | MIT | [LICENSE.txt](https://github.com/SanDisk-Open-Source/pyvcd/blob/master/LICENSE.txt) | Pulled in by Amaranth (which requires pyvcd below 0.5) |
| click | 8.5.1 | BSD-3-Clause | [LICENSE.txt](https://github.com/pallets/click/blob/main/LICENSE.txt) | Pulled in by yowasp-yosys |
| platformdirs | 4.14.5 | MIT | [LICENSE](https://github.com/tox-dev/platformdirs/blob/main/LICENSE) | Pulled in by yowasp-runtime |
| PyYAML | 6.0.3 | MIT | [LICENSE](https://github.com/yaml/pyyaml/blob/main/LICENSE) | Listed in the image pins |

### Ubuntu packages we install directly

Licenses are taken from each package's Debian copyright file (linked). Versions are what `apt-get install` picked on 2026-11-11; the image build uses a dated Ubuntu snapshot, and `SOURCES.txt` in the image lists the exact source package and version of everything installed.

| Package(s) | Source package and version | License (as verified) | Copyright file | Why it is in the image |
|---|---|---|---|---|
| `g++`, `gcc` | gcc-defaults 1.214ubuntu1 → gcc-13 13.3.0-6ubuntu2~24.04.1 | GPL-3.0-or-later; the runtime libraries add the GCC Runtime Library Exception 3.1 (some parts are GPL-2.0+ or LGPL-2.1+) | [gcc-13](https://changelogs.ubuntu.com/changelogs/pool/main/g/gcc-13/gcc-13_13.3.0-6ubuntu2~24.04.1/copyright), [gcc-defaults](https://changelogs.ubuntu.com/changelogs/pool/main/g/gcc-defaults/gcc-defaults_1.214ubuntu1/copyright) | Verilator compiles its C++ simulation models |
| `binutils` (installed by `g++`) | binutils 2.42-4ubuntu2.10 | GPL-3.0-or-later (manuals under GFDL-1.3-or-later) | [copyright](https://changelogs.ubuntu.com/changelogs/pool/main/b/binutils/binutils_2.42-4ubuntu2.10/copyright) | Assembler and linker that `g++` runs |
| `make` | make-dfsg 4.3-4.1build2 | GPL-3.0-or-later | [copyright](https://changelogs.ubuntu.com/changelogs/pool/main/m/make-dfsg/make-dfsg_4.3-4.1build2/copyright) | Verilator model builds |
| `ccache` | ccache 4.9.1-1 | GPL-3.0-or-later (a few files under other permissive licenses) | [copyright](https://changelogs.ubuntu.com/changelogs/pool/universe/c/ccache/ccache_4.9.1-1/copyright) | The official Verilator build expects it at run time |
| `perl` | perl 5.38.2-3.2ubuntu0.7 | GPL-1.0-or-later OR Artistic-1.0 ("GPL-1+ or Artistic") | [copyright](https://changelogs.ubuntu.com/changelogs/pool/main/p/perl/perl_5.38.2-3.2ubuntu0.7/copyright) | The `verilator` command is a Perl script |
| `python3`, `python3-venv`, `libpython3.12t64` | python3-defaults 3.12.3-0ubuntu2.1; python3.12 3.12.3-1ubuntu0.17 | PSF-2.0 | [python3.12](https://changelogs.ubuntu.com/changelogs/pool/main/p/python3.12/python3.12_3.12.3-1ubuntu0.17/copyright), [python3-defaults](https://changelogs.ubuntu.com/changelogs/pool/main/p/python3-defaults/python3-defaults_3.12.3-0ubuntu2.1/copyright) | Python for the worker; cocotb needs `libpython3.12.so` |
| `zlib1g-dev` | zlib 1:1.3.dfsg-3.1ubuntu2.3 | Zlib | [copyright](https://changelogs.ubuntu.com/changelogs/pool/main/z/zlib/zlib_1.3.dfsg-3.1ubuntu2.3/copyright) | Verilator model builds (FST wave output uses zlib) |
| `ca-certificates` | ca-certificates 20260816ubuntu0.24.04.2 | MPL-2.0 for the certificate data; GPL-2.0-or-later for the Debian scripts | [copyright](https://changelogs.ubuntu.com/changelogs/pool/main/c/ca-certificates/ca-certificates_20260816ubuntu0.24.04.2/copyright) | Root certificates for HTTPS |
| `iverilog` | iverilog 12.0-2build2 | GPL-2.0-or-later | see the Icarus Verilog row above | Simulator |

apt also installs their dependencies (for example libc6-dev, libstdc++-13-dev, libgmp10, libmpfr6, libmpc3), and the `ubuntu:24.04` base contains GPL tools such as bash and coreutils. All of them are listed with source package and version in `SOURCES.txt` and covered by rule 3.

## Python packages of the MCP server (host)

The server package depends on `mcp>=2.3,<3`, `pyyaml` and `pydantic>=2`. This is the dependency tree `uv tree` showed on 2026-11-11 for Linux and Python 3.12; `uv.lock` is authoritative. All are permissive. The `[amaranth]` extra adds amaranth, amaranth-yosys, wasmtime, Jinja2, MarkupSafe, jschon, rfc3986 and pyvcd, and `[myhdl]` adds myhdl, with the licenses shown in the tables above. Versions can differ from the image (for example wasmtime 50.0.1 on the host).

| Package | Version | License (as verified) | Official source |
|---|---|---|---|
| mcp | 2.3.0 | MIT | MCP Python SDK row above |
| mcp-types | 2.3.0 | MIT | [PyPI](https://pypi.org/project/mcp-types/2.3.0/) (same repository as mcp) |
| anyio | 4.16.0 | MIT | [LICENSE](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| httpx2, httpcore2 | 2.16.0 | BSD-3-Clause | [LICENSE.md](https://github.com/pydantic/httpx2/blob/main/LICENSE.md) |
| h11 | 0.16.0 | MIT | [LICENSE.txt](https://github.com/python-hyper/h11/blob/master/LICENSE.txt) |
| truststore | 0.10.5 | MIT | [LICENSE](https://github.com/sethmlarson/truststore/blob/main/LICENSE) |
| idna | 3.20 | BSD-3-Clause | [LICENSE.md](https://github.com/kjd/idna/blob/master/LICENSE.md) |
| jsonschema | 4.26.0 | MIT | [COPYING](https://github.com/python-jsonschema/jsonschema/blob/main/COPYING) |
| jsonschema-specifications | 2025.9.1 | MIT | [COPYING](https://github.com/python-jsonschema/jsonschema-specifications/blob/main/COPYING), [PyPI](https://pypi.org/project/jsonschema-specifications/2025.9.1/) |
| referencing | 0.37.0 | MIT | [COPYING](https://github.com/python-jsonschema/referencing/blob/main/COPYING) |
| rpds-py | 2026.9.1 | MIT | [LICENSE](https://github.com/crate-py/rpds/blob/main/LICENSE) |
| attrs | 26.1.0 | MIT | [LICENSE](https://github.com/python-attrs/attrs/blob/main/LICENSE) |
| pydantic | 2.14.3 | MIT | [LICENSE](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| pydantic-core | 2.50.4 | MIT | [LICENSE](https://github.com/pydantic/pydantic-core/blob/main/LICENSE) |
| annotated-types | 0.8.0 | MIT | [LICENSE](https://github.com/annotated-types/annotated-types/blob/main/LICENSE) |
| typing-inspection | 0.4.5 | MIT | [LICENSE](https://github.com/pydantic/typing-inspection/blob/main/LICENSE) |
| typing-extensions | 4.16.0 | PSF-2.0 | [LICENSE](https://github.com/python/typing_extensions/blob/main/LICENSE), [PyPI](https://pypi.org/project/typing-extensions/4.16.0/) |
| opentelemetry-api | 1.45.2 | Apache-2.0 | [LICENSE](https://github.com/open-telemetry/opentelemetry-python/blob/main/LICENSE) |
| PyJWT (with the `crypto` extra) | 2.15.1 | MIT | [LICENSE](https://github.com/jpadilla/pyjwt/blob/master/LICENSE) |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | [LICENSE](https://github.com/pyca/cryptography/blob/main/LICENSE), [PyPI](https://pypi.org/project/cryptography/50.0.2/) |
| cffi | 2.1.1 | MIT-0 | [LICENSE](https://github.com/python-cffi/cffi/blob/main/LICENSE), [PyPI](https://pypi.org/project/cffi/2.1.1/) |
| pycparser | 3.12 | BSD-3-Clause | [LICENSE](https://github.com/eliben/pycparser/blob/main/LICENSE), [PyPI](https://pypi.org/project/pycparser/3.12/) |
| python-multipart | 0.0.33 | Apache-2.0 | [LICENSE.txt](https://github.com/Kludex/python-multipart/blob/main/LICENSE.txt) |
| sse-starlette | 3.7.0 | BSD-3-Clause | [LICENSE](https://github.com/sysid/sse-starlette/blob/main/LICENSE) |
| starlette | 1.10.0 | BSD-3-Clause | [LICENSE.md](https://github.com/Kludex/starlette/blob/main/LICENSE.md) |
| uvicorn | 0.54.0 | BSD-3-Clause | [LICENSE.md](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| click | 8.5.1 | BSD-3-Clause | [LICENSE.txt](https://github.com/pallets/click/blob/main/LICENSE.txt) |
| PyYAML | 6.0.3 | MIT | [LICENSE](https://github.com/yaml/pyyaml/blob/main/LICENSE) |

## Rules

1. GPL tools (Icarus, KLayout, Xschem, netgen, openEMS, Elmer, OpenSTA inside OpenROAD, and any other GPL tool we add) are only ever called as **separate processes**. Never import, link, or copy their source into this repo.
2. LGPL (MyHDL; Verilator as a process; ngspice subcomponents we never link) is fine as an unmodified dependency or external binary. Keep MyHDL an optional extra.
3. **Corresponding source for the image.** This repo contains no tool source or binaries. The Docker image is a separate distribution that contains GPL and LGPL binaries, so every published image ships, in `/usr/share/doc/siliconsmith-toolchain/`:
   - `SOURCES.txt`: for each unmodified Ubuntu package, its source package name and version (which identifies the exact source in the Ubuntu archive); for Verilator and every pip package, the upstream URL at the exact tag or version;
   - a copy of this file and the license files of bundled components (wellen, Yosys, ABC, yosys-slang, slang);
   - a written offer to provide the complete corresponding source on request, valid for at least three years (as [GPLv2 section 3(b)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt) requires).

   The written offer covers the GPL-2.0 components (for example Icarus Verilog). For the GPL-3.0 components (for example gcc-13, make, ccache and binutils), `SOURCES.txt` is the "clear directions" that [GPLv3 section 6(d)](https://www.gnu.org/licenses/gpl-3.0.txt) requires when object code is offered for download: it points at the exact source versions in the Ubuntu archive (including [snapshot.ubuntu.com](https://snapshot.ubuntu.com/)) and at the upstream tags. We remain responsible for that source staying available. We distribute Verilator under its Artistic-2.0 option; section 5 of that license asks only for complete instructions on how to get the source, which `SOURCES.txt` gives. A hosted source bundle per image is planned (M5-3) as the backstop if an Ubuntu or upstream source disappears. Images we only reference by digest and never re-publish, such as LibreLane's, are distributed by their own projects.
4. If a license cannot be verified from the official source, do not add the tool until it can.
