# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""TEST-ONLY: run the worker's equivalence check on an RTL folder and a netlist file.

Runs INSIDE the toolchain container, started the way the runner starts it (see
`tests/integration/helpers.hardened`), with this folder mounted at /smoke. The production `equiv`
verb has no way to substitute a netlist; this script is how tests give `check` a hand-edited one.

    python /smoke/check_netlist.py RTL_DIR NAME NETLIST SCRATCH [DEADLINE_S]

RTL_DIR holds `project.v` and `<NAME>.v` (for example the project's `/work/src`), NETLIST is a
sky130 netlist file, SCRATCH a folder the check may write to (for example under /work/build).
Prints the verdict dict as one JSON line and exits 0 whatever the verdict is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from siliconsmith.worker import equiv


def main(argv: list[str]) -> int:
    if len(argv) not in (5, 6):
        print(__doc__, file=sys.stderr)
        return 2
    rtl_dir, name, netlist, scratch = argv[1:5]
    deadline = float(argv[5]) if len(argv) == 6 else equiv.DEADLINE_S
    verdict = equiv.check(Path(rtl_dir), name, Path(netlist), Path(scratch), deadline_s=deadline)
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
