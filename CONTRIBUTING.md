# Contributing to Siliconsmith

If you are an AI agent, or you are implementing a milestone, read [CLAUDE.md](CLAUDE.md) and
[PLAN.md](PLAN.md) first, then work milestone by milestone against the issues in
[ISSUES.md](ISSUES.md) and on the GitHub tracker.

To report a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## Dev setup

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/RFingAdam/siliconsmith
cd siliconsmith
uv sync
```

Run these before you push. CI runs the same commands.

```sh
uv sync --locked
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

`check_trailers.py` treats a local run like a branch of this repository, so it rejects
`Signed-off-by` lines. On a fork, tell it so:
`HEAD_REPO=<you>/siliconsmith BASE_REPO=RFingAdam/siliconsmith uv run python scripts/check_trailers.py origin/main`.
`scripts/check_cla.py` reads the same `HEAD_REPO` and `BASE_REPO` plus `PR_BODY`.

### Integration tests

The integration tests run the real tools in the toolchain container, so they need Docker and the
toolchain image named in the README's "Toolchain image" section:

```sh
docker pull ghcr.io/rfingadam/siliconsmith-toolchain:<tag from the README>
uv run pytest -m integration
```

Set `SILICONSMITH_IMAGE` to test against an image with another name.

### What CI runs

- **lint** (Python 3.12): the command list above except pytest, plus a pip-compat check that runs
  `pip install -e '.[amaranth,myhdl]'` in a fresh virtual environment and checks that the
  commands start and the packages import. The trailer and CLA checks run on pull requests only.
- **unit** (Python 3.11 and 3.14): `uv run pytest -m "not integration"`.
- **integration**: pulls the toolchain image (or builds it when the pull fails, for example
  because a new tag is not published yet) and runs `uv run pytest -m integration`.
- **image** (a separate workflow, only when `docker/` or `image.yml` changes): builds and smoke-tests
  the image on pull requests, and publishes it to GHCR on `main`.

### Files that must stay in sync

- **The image tag.** It is a hash of the files under `docker/`, so any change there gives a new
  tag. `uv run python -m siliconsmith._imagetag` prints the hash. Update `DEFAULT_IMAGE` in
  `src/siliconsmith/runners/docker.py` and every copy of the tag in `README.md`.
  `tests/test_image_pins.py` checks `DEFAULT_IMAGE` and every copy of the tag in the README.
- **The glossary.** `docs/glossary.md` must be byte-identical to
  `src/siliconsmith/skills/glossary.md`, which `explain` serves (`tests/test_skills.py`).
- **Third-party licenses.** `docker/THIRD_PARTY_LICENSES.md` must be a copy of
  `THIRD_PARTY_LICENSES.md` (`tests/test_image_pins.py`).
- **The README snippets.** The agent configuration snippets in `README.md` are launched by
  `tests/test_readme_snippets.py`. Keep each `<!-- snippet: name -->` marker on the line right
  before its code block.

## License rules

- Siliconsmith's own code is AGPL-3.0-or-later. Every new source, test and script file gets an
  SPDX header: `# SPDX-FileCopyrightText: <year> <name>` and
  `# SPDX-License-Identifier: AGPL-3.0-or-later`. `reuse lint` checks this. Markdown files are
  covered by `REUSE.toml`.
- These parts stay Apache-2.0, so that users' chip designs are never encumbered: the templates
  (`src/siliconsmith/templates/`), the skills (`src/siliconsmith/skills/`) and `docs/glossary.md`,
  and example designs (`examples/`). New files there get an `Apache-2.0` header.
  `tests/test_license_headers.py` fails when a file has the wrong license for its path.
- Files and text that Siliconsmith writes into a user's project keep their `Apache-2.0` header
  lines. Do not change those strings. The additional permission in `OUTPUT-EXCEPTION.md` makes
  them accurate; it covers all of Siliconsmith's code, so no per-file notice is needed. A module
  that writes into a user's project carries a header comment that points to that file, and a new
  one should too. Captured generator output under `tests/fixtures/` keeps the `Apache-2.0` header
  the generator wrote (list it in `SPECIAL` in `tests/test_license_headers.py`).
- Open-source tools only. No commercial EDA, even as an option.
- GPL tools are allowed only as external processes (a subprocess or `docker run`). Never import
  or link them into this code. In particular, never import the KLayout Python module. This keeps
  the sandbox model and keeps the maintainer able to offer a commercial license, which is not
  possible for GPL code.
- LGPL code (MyHDL) may be used as an unmodified dependency or external binary, as
  `THIRD_PARTY_LICENSES.md` rule 2 says. Keep MyHDL an optional extra.
- Never vendor tool source code or binaries into this repository. CI fails on binary files and on
  files over 1 MB.
- Never copy code from a repository that has no license.
- New tools are recorded in `THIRD_PARTY_LICENSES.md` after checking the license at the project's
  official source.

## Contributor License Agreement (CLA)

Outside contributors (pull requests from a fork) must agree to [CLA.md](CLA.md). Tick the first
box in the pull request template to do so. The CLA lets the maintainer relicense contributions,
including under a commercial license, and you keep your own copyright. CI fails a fork pull
request whose description does not have that box ticked (`scripts/check_cla.py`). Pull requests
from branches of this repository (the owner's and AI agents') and Dependabot are exempt. There is
no DCO, but a `Signed-off-by` line on a fork pull request is allowed.

CI also scans the commit messages and the description of every pull request. It always rejects
`Co-authored-by` lines, "Generated with" or "Generated by" footers, and any `...-by:` trailer that
names an AI tool. On branches in this repository (the owner's and AI agents'), it also rejects
`Signed-off-by` lines; on fork PRs it accepts them. The check looks only at lines that start like
a trailer, so ordinary sentences that mention these words are fine. Dependabot PRs are exempt from
the scan, because Dependabot always adds its own `Signed-off-by` line.

## Pull requests

- One issue per PR. Name the branch after the issue (for example `m0-1-skeleton`).
- CI must be green before merge.
- Tick the finished boxes in `ISSUES.md` and in the GitHub issue body, then close the issue with a
  comment that links the PR.
- Do not force-push or rebase a published branch. Merge `main` into it instead.
