# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Code that runs inside the toolchain container (`python -m siliconsmith.worker <verb>`).

Stdlib only at module level: the container has no `mcp` or `pydantic`. Packages that exist only
in the image (cocotb, amaranth, ...) are imported inside the functions that need them.
"""
