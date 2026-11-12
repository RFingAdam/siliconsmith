# Contributing to Siliconsmith

## Dev setup

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). Docker is only needed for the
integration tests that arrive in later M0 pull requests.

```sh
git clone https://github.com/RFingAdam/siliconsmith
cd siliconsmith
uv sync
```

Run these before you push. CI runs the same commands.

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uvx --from 'reuse[charset-normalizer]==6.3.0' reuse lint
uv run python scripts/check_api_guard.py
uv run python scripts/check_no_binaries.py
uv run python scripts/check_wheel.py
uv run python scripts/check_trailers.py origin/main
uv run pytest -m "not integration"
```

`uv run ruff format` rewrites files to the project style. `uv sync --locked` fails if `uv.lock`
is out of date; run `uv lock` after you change dependencies in `pyproject.toml`.

## License rules

- The project is Apache-2.0. Every new source file gets an SPDX header:
  `# SPDX-FileCopyrightText: <year> <name>` and `# SPDX-License-Identifier: Apache-2.0`.
  `reuse lint` checks this.
- Open-source tools only. No commercial EDA, even as an option.
- GPL and LGPL tools are allowed only as external processes (a subprocess or `docker run`).
  Never import or link them into this code. In particular, never import the KLayout Python
  module.
- Never vendor tool source code or binaries into this repository. CI fails on binary files and on
  files over 1 MB.
- Never copy code from a repository that has no license.
- New tools are recorded in `THIRD_PARTY_LICENSES.md` after checking the license at the project's
  official source.

## Sign-off (DCO)

Contributions from outside contributors (pull requests from a fork) must be signed off with
`git commit -s`. This adds a `Signed-off-by:` line and means you agree to the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).

On branches in this repository (the owner's and AI agents'), commits and PR descriptions must not
contain `Co-authored-by`, `Signed-off-by` or "Generated with" lines. CI scans them. The check
looks at lines that start like a trailer, so ordinary sentences that mention these words are fine.
On fork PRs CI accepts `Signed-off-by` and still rejects the other two. Dependabot PRs are exempt
from the scan, because Dependabot always adds its own `Signed-off-by` line.

## Pull requests

- One issue per PR. Name the branch after the issue (for example `m0-1-skeleton`).
- CI must be green before merge.
- Tick the finished boxes in `ISSUES.md` and in the GitHub issue body, then close the issue with a
  comment that links the PR.
- Do not force-push or rebase a published branch. Merge `main` into it instead.
