# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Open this repository's
[Security and quality](https://github.com/RFingAdam/siliconsmith/security) tab on GitHub and
choose **Report a vulnerability**. The report stays private between you and the maintainers.

If that button is missing (private reporting is switched off), open an issue that asks for a
security contact, and leave out the details.

A good report says:

- what an attacker controls (a design file, a test, a tool argument, a project folder name, the
  contents of `build/`, ...);
- what they can reach that they should not (a file outside the project, the network, the Docker
  daemon, a forged ledger entry, ...);
- the Siliconsmith commit hash (`git rev-parse HEAD` in a clone, or the commit you installed
  from; the version number is the same at every commit), the toolchain image tag, your Docker
  version, and whether Docker runs rootless;
- the smallest steps or project that show it.

This is a small project, so a reply may take a few days.

## Supported versions

There are no releases yet (the version is `0.1.0.dev0`). Security fixes land on the `main`
branch; a fix inside the toolchain image comes with a new image tag, named in the README. Please
check that a problem still exists on `main` before you report it.

## How Siliconsmith protects your machine

- **Transport.** The MCP server talks to the agent over stdin and stdout only; it does not listen
  on the network.
- **What runs in a container.** Code the agent wrote: an Amaranth or MyHDL design (Python that
  runs while it is turned into Verilog) and cocotb tests. The server never imports that code.
- **Container flags.** Each step gets a fresh container from the pinned toolchain image, started
  with a fixed argument list (no shell): `--network none`, `--read-only`, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--pids-limit 512`, `--memory 4g` with no extra swap,
  `--cpus 2`, limits on file size and open files, a 1 GB in-memory `/tmp`, and `--pull never`.
  It runs as your user ID (or under rootless Docker's own mapping). Siliconsmith refuses to start
  a container when it runs as root against a rootful Docker daemon.
- **Mounts.** The project's `hdl/`, `src/`, `test/`, `spec.yaml` and `info.yaml` are read-only;
  only `build/` is writable. The Docker socket is never mounted.
- **Host file access.** Every file read and write goes through one function that rejects absolute
  paths, `..` and NUL bytes, does not follow symlinks, accepts only regular files with a single
  hard link, and caps sizes.
- **Container output.** Treated as untrusted: the result file is size-capped and validated.

The full threat model is in [PLAN.md section 5.1](PLAN.md#51-threat-model-and-sandbox).

## In scope

- The MCP server and its tools: argument checks, the per-front-end write allow-lists, and project
  root selection.
- Path handling: anything that lets a tool, a design or a test read or write outside the project,
  or plant a file that your machine later runs (git hooks, CI workflows, editor settings).
- The container runner (`src/siliconsmith/runners/docker.py`): its flags, its mounts, time-outs
  and clean-up, and the parsing of results that come out of the container.
- The validation ledger and `status`: a way for code in the container, or a tool argument, to
  write `.siliconsmith/ledger.json` (or any file outside `build/`), to get a check recorded that
  never ran, or to get a result recorded against files other than the current ones. A test that
  lies about the result of its own run is out of scope (below).
- The `siliconsmith` command line, including `status` and `approve`.
- The toolchain image build (`docker/`) and the CI workflows: pins, hash checks, and anything that
  could let unreviewed code into the published image.

## Out of scope

- **Bugs in the third-party tools themselves** (Yosys, Icarus Verilog, Verilator, cocotb,
  Amaranth, MyHDL and the others in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)). Please
  report those to the upstream project. If one of them lets code escape *our* container settings,
  that part is in scope.
- **Docker or Linux kernel escapes.** The sandbox relies on Docker and the kernel; report those to
  their maintainers.
- **An agent with its own shell or file tools.** If your agent can run commands as you (for
  example Claude Code's Bash tool), it can already do anything you can. The sandbox only covers
  what runs through Siliconsmith.
- **Spec approval.** `siliconsmith approve` is an honest-use convention, not a security boundary.
  It keeps an honest agent from approving its own work and records who approved which version of
  the spec. Anything that can type into your terminal can approve.
- **A design or test that fakes the result of its own run** (for example a cocotb test that
  reports a pass without testing anything). The checks catch honest mistakes, not a hostile
  design. That code must still not be able to reach your machine, the network or files outside
  `build/`.
- **Exposing the server to other users.** Whoever can call the tools can start containers, so only
  you should reach the server. It speaks stdio only; a network transport with authentication is
  planned for M5-3.
