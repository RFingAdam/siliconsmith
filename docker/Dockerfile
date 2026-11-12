# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
#
# Siliconsmith toolchain image: simulation stage (M0) plus the ten sky130 files that synthesis and
# gate-level simulation need (M1). linux/amd64 only.
# The build context is docker/ and nothing else: the image tag is a hash of this folder.

# Ubuntu archive snapshot, shared by the pdk and final stages (each stage re-declares the ARG).
ARG APT_SNAPSHOT=20261110T000000Z

# Both base images are pinned by digest, and Dependabot bumps them (.github/dependabot.yml).
# Verilator v5.046 comes from the official image because Ubuntu 24.04's apt Verilator (5.020) is
# older than the 5.036 that cocotb 2.1 needs. We copy its files; we never run that image.
FROM verilator/verilator:v5.046@sha256:8003e282e9ff02eb446083eb5a3c512225a269f0a7b46a7e392c99468e890018 AS verilator

# Builder stage (thrown away; nothing from it is installed in the final image except the files
# copied out below). It downloads two PDK tarballs from the fossi-foundation/ciel-releases
# GitHub release for open_pdks 8afc8346... (the sky130 version LibreLane 3.0.14 and Tiny Tapeout
# harden with), checks each against a sha256 pinned here, and keeps only the files M1 needs.
# ca-certificates comes from the live Ubuntu archive, curl and zstd from the apt snapshot; none of
# them reach the final image.
# The pins here must equal tt.lock (tests/test_image_pins.py checks the hash).
FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5 AS pdk
ARG SKY130_OPEN_PDKS=8afc8346a57fe1ab7934ba5a6056ea8b43078e71
ARG PDK_COMMON_SHA256=be8a548341fd9177af9e461ad7b4fdd89afbe03dba68dc42d155321fdc3f86bf
ARG PDK_HD_SHA256=17047505cfff30841bf53960b8cbf9e7c79b2a3bbb2ebec0bb254eb441e4ce1e
ARG APT_SNAPSHOT
ENV DEBIAN_FRONTEND=noninteractive
# Same pattern as the final stage: ca-certificates from the live archive, the rest from the snapshot.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && apt-get update --snapshot "${APT_SNAPSHOT}" \
 && apt-get install -y --no-install-recommends curl zstd \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /dl
RUN base="https://github.com/fossi-foundation/ciel-releases/releases/download/sky130-${SKY130_OPEN_PDKS}" \
 && curl -fsSL --retry 3 -o common.tar.zst "${base}/common.tar.zst" \
 && curl -fsSL --retry 3 -o sky130_fd_sc_hd.tar.zst "${base}/sky130_fd_sc_hd.tar.zst" \
 && printf '%s  common.tar.zst\n%s  sky130_fd_sc_hd.tar.zst\n' \
      "${PDK_COMMON_SHA256}" "${PDK_HD_SHA256}" > SHA256SUMS \
 && sha256sum --strict -c SHA256SUMS
# Only these 10 files are kept (about 17 MB of the 134 MB download): the typical-corner liberty file,
# the two Verilog cell models for gate-level simulation, and the files LibreLane 3.0.14's
# synthesis step reads for sky130 (do-not-use list, DRC-exclude list, and the latch, tri-state,
# full-adder and ripple/carry-select adder technology maps).
ENV HD=sky130A/libs.ref/sky130_fd_sc_hd \
    OL=sky130A/libs.tech/openlane/sky130_fd_sc_hd
RUN mkdir -p /pdk \
 && zstd -dc common.tar.zst | tar -x --no-same-owner -C /pdk \
      "${OL}/no_synth.cells" "${OL}/drc_exclude.cells" "${OL}/latch_map.v" \
      "${OL}/tribuff_map.v" "${OL}/fa_map.v" "${OL}/rca_map.v" "${OL}/csa_map.v" \
 && zstd -dc sky130_fd_sc_hd.tar.zst | tar -x --no-same-owner -C /pdk \
      "${HD}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
      "${HD}/verilog/sky130_fd_sc_hd.v" "${HD}/verilog/primitives.v" \
 && cd /pdk \
 && find sky130A -type f | sort > /tmp/files.txt \
 && test "$(wc -l < /tmp/files.txt)" = 10 \
 && { \
      printf '{\n  "name": "sky130A",\n  "open_pdks": "%s",\n' "${SKY130_OPEN_PDKS}"; \
      printf '  "libraries": ["sky130_fd_sc_hd"],\n  "corner": "tt_025C_1v80",\n  "files": {\n'; \
      sha256sum $(cat /tmp/files.txt) | awk '{printf "%s    \"%s\": \"%s\"", (NR>1?",\n":""), $2, $1}'; \
      printf '\n  }\n}\n'; \
    } > manifest.json \
 && chmod -R a-w,a+rX /pdk

FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5

# Ubuntu archive snapshot to install from. `apt-get update --snapshot` talks https to
# snapshot.ubuntu.com, and the base image has no root certificates, so ca-certificates is
# installed first from the live archive. That step also pulls in openssl and a newer libssl3t64
# from the live archive, and they stay. Everything else comes from the snapshot, so rebuilding
# later gives the same packages apart from those three.
ARG APT_SNAPSHOT
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && apt-get update --snapshot "${APT_SNAPSHOT}" \
 && apt-get install -y --no-install-recommends \
      iverilog=12.0-2build2 python3 python3-venv libpython3.12t64 \
      g++ make perl ccache zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

# Verilator: the commands, its data files and its pkg-config file. The debug builds
# (verilator_bin_dbg and friends, about 100 MB) are left out, and so is verilator_coverage,
# which cannot run without its debug binary.
COPY --from=verilator /usr/local/bin/verilator /usr/local/bin/verilator_bin \
     /usr/local/bin/verilator_gantt /usr/local/bin/verilator_profcfunc /usr/local/bin/
COPY --from=verilator /usr/local/share/verilator /usr/local/share/verilator
COPY --from=verilator /usr/local/share/pkgconfig/verilator.pc /usr/local/share/pkgconfig/verilator.pc

# The sky130 files from the builder stage, read-only, plus /opt/pdk/manifest.json (what is in
# there and the sha256 of each file; `toolchain_info` and the integration tests read it).
COPY --from=pdk /pdk/ /opt/pdk/

# Python packages, hash-locked. --no-deps because the lock lists every dependency.
# The byte-compile step matters: the container runs with a read-only root and
# PYTHONDONTWRITEBYTECODE=1, so without it every import would compile from source.
COPY requirements.lock /tmp/requirements.lock
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
 && /opt/venv/bin/python -m compileall -q /opt/venv/lib \
 && rm /tmp/requirements.lock

# Yosys 0.62 (YoWASP, WebAssembly) behind the name `yosys`. The shim first makes sure the
# pre-compiled WebAssembly cache is in place (see ss-seed-cache).
COPY --chmod=755 ss-seed-cache /usr/local/bin/ss-seed-cache
RUN printf '#!/bin/sh\nss-seed-cache\nexec yowasp-yosys "$@"\n' > /usr/local/bin/yosys \
 && chmod 755 /usr/local/bin/yosys

ENV PATH=/opt/venv/bin:$PATH \
    AMARANTH_USE_YOSYS=builtin \
    PYTHONDONTWRITEBYTECODE=1

# Compile both WebAssembly Yosys builds to machine code now and keep the result in
# /opt/siliconsmith-cache. YoWASP caches in $XDG_CACHE_HOME/YoWASP and amaranth-yosys in
# $XDG_CACHE_HOME/wasmtime, so one XDG_CACHE_HOME covers both.
RUN mkdir -p /opt/siliconsmith-cache /tmp/warm-home \
 && HOME=/tmp/warm-home XDG_CACHE_HOME=/opt/siliconsmith-cache yowasp-yosys -V \
 && HOME=/tmp/warm-home XDG_CACHE_HOME=/opt/siliconsmith-cache python -m amaranth_yosys -V \
 && chmod -R a+rX /opt/siliconsmith-cache \
 && rm -rf /tmp/warm-home

# Source and license information for everything above (see OFFER.txt for the written offer).
COPY gen_sources.py Dockerfile /tmp/
COPY licenses /usr/share/doc/siliconsmith-toolchain/licenses
COPY THIRD_PARTY_LICENSES.md OFFER.txt /usr/share/doc/siliconsmith-toolchain/
RUN APT_SNAPSHOT="${APT_SNAPSHOT}" \
    /opt/venv/bin/python /tmp/gen_sources.py /usr/share/doc/siliconsmith-toolchain \
      /opt/venv/lib/python3.12/site-packages \
 && rm /tmp/gen_sources.py /tmp/Dockerfile \
 && chmod -R a+rX /usr/share/doc/siliconsmith-toolchain

# The licenses label is a LicenseRef because the image mixes many licenses; the full list is in
# /usr/share/doc/siliconsmith-toolchain/THIRD_PARTY_LICENSES.md. The version label is cleared
# because the Ubuntu base image sets it to 24.04, which is not our version.
LABEL org.opencontainers.image.source="https://github.com/RFingAdam/siliconsmith" \
      org.opencontainers.image.licenses="LicenseRef-siliconsmith-toolchain-mixed" \
      org.opencontainers.image.version="" \
      org.opencontainers.image.description="Siliconsmith toolchain: Icarus Verilog, Verilator, Yosys, cocotb, Amaranth, MyHDL and ten sky130 files for synthesis and gate-level simulation"

WORKDIR /work
