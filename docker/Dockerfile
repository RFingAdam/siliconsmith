# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
#
# Siliconsmith toolchain image, simulation stage (M0). linux/amd64 only.
# The build context is docker/ and nothing else: the image tag is a hash of this folder.

# Both base images are pinned by digest, and Dependabot bumps them (.github/dependabot.yml).
# Verilator v5.046 comes from the official image because Ubuntu 24.04's apt Verilator (5.020) is
# older than the 5.036 that cocotb 2.1 needs. We copy its files; we never run that image.
FROM verilator/verilator:v5.054@sha256:da7845c38c2ab84133661a1b1e2d717d028fe0bc629cc06c77bb80571e610448 AS verilator

FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5

# Ubuntu archive snapshot to install from. `apt-get update --snapshot` talks https to
# snapshot.ubuntu.com, and the base image has no root certificates, so ca-certificates is
# installed first from the live archive. That step also pulls in openssl and a newer libssl3t64
# from the live archive, and they stay. Everything else comes from the snapshot, so rebuilding
# later gives the same packages apart from those three.
ARG APT_SNAPSHOT=20261110T000000Z
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
      org.opencontainers.image.description="Siliconsmith toolchain, simulation stage: Icarus Verilog, Verilator, Yosys, cocotb, Amaranth and MyHDL"

WORKDIR /work
