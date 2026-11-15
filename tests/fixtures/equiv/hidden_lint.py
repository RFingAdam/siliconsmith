# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TEST-ONLY: run the worker's lint without comments (`equiv._hidden_lint`) on an RTL folder.

Runs INSIDE the toolchain container, like `check_netlist.py`, with this folder mounted at /smoke.
The text rule in `equiv_sv` refuses the lint_off spellings it can read before the check gets to
this lint, so this script calls the lint directly to show that the real Verilator finds a race
that a comment hid.

    python /smoke/hidden_lint.py RTL_DIR NAME SCRATCH

Prints `[reason, sentence]` or `null` as one JSON line and exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from siliconsmith.worker import equiv


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    rtl_dir, name, scratch = Path(argv[1]), argv[2], Path(argv[3])
    sources = [
        (fname, (rtl_dir / fname).read_bytes().decode("latin-1"))
        for fname in ("project.v", f"{name}.v")
    ]
    print(json.dumps(equiv._hidden_lint(scratch, sources, name)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
