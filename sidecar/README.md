# Sidecar CLI Build Guide

This document explains how to build the packaged Sidecar CLI binary for different OS/CPU combinations.

## What This Build Produces

Running the packaged build script creates:

- Launcher binary: `sidecar/dist/nsbot`
- Python payload binary: `sidecar/dist/binaries/nsbot-sidecar-cli-payload` (or `.exe` on Windows)
- Runtime assets:
  - `sidecar/dist/runtime/templates`
  - `sidecar/dist/runtime/search-tools/{fd,rg}`

The launcher is built by Rust (`src-tauri`), and the payload is built by PyInstaller.

## Build Matrix

The host target is auto-detected by `sidecar/scripts/build_packaged_cli.sh`.

| Host OS | Host CPU | Auto target triple | Current status |
|---|---|---|---|
| macOS | arm64 (Apple Silicon) | `aarch64-apple-darwin` | Supported |
| macOS | x86_64 (Intel) | `x86_64-apple-darwin` | Supported |
| Linux | x86_64 | `x86_64-unknown-linux-gnu` | Script has a known limitation (see below) |
| Linux | aarch64 | `aarch64-unknown-linux-gnu` | Script has a known limitation (see below) |
| Windows (Git Bash/MSYS2) | x86_64 | `x86_64-pc-windows-msvc` | Supported |

### Known Linux Limitation

`sidecar/scripts/prepare_search_tools.py` currently supports vendoring `fd`/`rg` for:

- `aarch64-apple-darwin`
- `x86_64-apple-darwin`
- `x86_64-pc-windows-msvc`

It does not yet include Linux target entries, so `build_packaged_cli.sh` can fail on Linux during search-tool preparation.

## Prerequisites

## All Platforms

1. Python `>= 3.11`
2. `uv` installed and available in PATH
3. Rust toolchain (`cargo`) installed
4. Network access on first build (to download vendored `fd`/`rg`)

## macOS

1. Install Xcode Command Line Tools:
   - `xcode-select --install`
2. Install `uv` and Rust (example):
   - `brew install uv`
   - `brew install rustup-init && rustup-init -y`

## Linux

1. Install Python 3.11+, `curl`, `build-essential`, and OpenSSL dev headers
2. Install `uv` and Rust
3. Note the Linux limitation above before running packaged build

## Windows (MSVC)

1. Install Python 3.11+
2. Install Rust MSVC toolchain (`rustup default stable-x86_64-pc-windows-msvc`)
3. Use Git Bash or MSYS2 to run the shell script
4. Install `uv` for Windows

## Build Commands

Run from repository root:

```bash
bash sidecar/scripts/build_packaged_cli.sh
```

Optional: run provider/model smoke checks during the build:

```bash
NSBOT_RUN_PROVIDER_MODEL_SMOKE=1 bash sidecar/scripts/build_packaged_cli.sh
```

Or run from `sidecar`:

```bash
bash scripts/build_packaged_cli.sh
```

## Verify Build Output

From repository root:

```bash
ls -la sidecar/dist
ls -la sidecar/dist/binaries
ls -la sidecar/dist/runtime/search-tools
sidecar/dist/nsbot --help
```

## Optional: Run Packaged CLI E2E

```bash
bash sidecar/tests/e2e_packaged_cli.sh
bash sidecar/tests/e2e_agent_cli.sh
```

## Troubleshooting

## `uv: command not found`

Install `uv` and make sure it is in PATH.

## `cargo: command not found`

Install Rust toolchain and ensure `cargo` is in PATH.

## Missing payload or launcher in `sidecar/dist`

Re-run:

```bash
bash sidecar/scripts/build_packaged_cli.sh
```

Then inspect logs for failing stage (`PyInstaller`, `prepare_search_tools.py`, or `cargo build`).

## Linux build fails while preparing search tools

This is expected with current script support. Add Linux target entries in `sidecar/scripts/prepare_search_tools.py` (fd/rg release mapping) before relying on Linux packaged builds.
