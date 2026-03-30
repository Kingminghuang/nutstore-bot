#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FD_VERSION = "v10.4.2"
DEFAULT_RG_VERSION = "15.1.0"


@dataclass(frozen=True)
class SearchToolRelease:
    archive_kind: str
    archive_name: str
    download_url: str
    binary_name: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare vendored fd/rg binaries for sidecar development."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent.parent,
        help="Repository root directory.",
    )
    parser.add_argument("--target", required=True, help="Rust target triple.")
    parser.add_argument(
        "--fd-version", default=DEFAULT_FD_VERSION, help="fd release version."
    )
    parser.add_argument(
        "--rg-version", default=DEFAULT_RG_VERSION, help="ripgrep release version."
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite cached tool binaries."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    destination_root = repo_root / "sidecar" / "vendor" / "search-tools" / args.target

    prepare_tool_directory(
        destination=destination_root / "fd",
        release=fd_release_for_target(args.target, args.fd_version),
        force=args.force,
    )
    prepare_tool_directory(
        destination=destination_root / "rg",
        release=rg_release_for_target(args.target, args.rg_version),
        force=args.force,
    )
    return 0


def fd_release_for_target(target: str, version: str) -> SearchToolRelease:
    supported = {
        "aarch64-apple-darwin": ("tar.gz", f"fd-{version}-aarch64-apple-darwin.tar.gz", "fd"),
        "x86_64-apple-darwin": ("tar.gz", f"fd-{version}-x86_64-apple-darwin.tar.gz", "fd"),
        "x86_64-pc-windows-msvc": ("zip", f"fd-{version}-x86_64-pc-windows-msvc.zip", "fd.exe"),
    }
    archive_kind, archive_name, binary_name = require_supported_target("fd", target, supported)
    return SearchToolRelease(
        archive_kind=archive_kind,
        archive_name=archive_name,
        download_url=f"https://github.com/sharkdp/fd/releases/download/{version}/{archive_name}",
        binary_name=binary_name,
    )


def rg_release_for_target(target: str, version: str) -> SearchToolRelease:
    supported = {
        "aarch64-apple-darwin": ("tar.gz", f"ripgrep-{version}-aarch64-apple-darwin.tar.gz", "rg"),
        "x86_64-apple-darwin": ("tar.gz", f"ripgrep-{version}-x86_64-apple-darwin.tar.gz", "rg"),
        "x86_64-pc-windows-msvc": ("zip", f"ripgrep-{version}-x86_64-pc-windows-msvc.zip", "rg.exe"),
    }
    archive_kind, archive_name, binary_name = require_supported_target(
        "ripgrep", target, supported
    )
    return SearchToolRelease(
        archive_kind=archive_kind,
        archive_name=archive_name,
        download_url=f"https://github.com/BurntSushi/ripgrep/releases/download/{version}/{archive_name}",
        binary_name=binary_name,
    )


def require_supported_target(
    tool_name: str, target: str, supported: dict[str, tuple[str, str, str]]
) -> tuple[str, str, str]:
    if target not in supported:
        supported_targets = ", ".join(sorted(supported))
        raise SystemExit(
            f"unsupported {tool_name} target {target}; supported targets: {supported_targets}"
        )
    return supported[target]


def prepare_tool_directory(destination: Path, release: SearchToolRelease, force: bool) -> None:
    if (
        destination.exists()
        and not force
        and locate_extracted_binary(destination, release.binary_name) is not None
    ):
        return

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nsbot-search-tool-") as temp_dir:
        archive_path = Path(temp_dir) / release.archive_name
        extraction_root = Path(temp_dir) / "extract"
        extraction_root.mkdir(parents=True, exist_ok=True)
        download_to_path(release.download_url, archive_path)
        extract_archive(archive_path, extraction_root, release.archive_kind)
        binary_path = locate_extracted_binary(extraction_root, release.binary_name)
        if binary_path is None:
            raise SystemExit(
                f"unable to locate extracted binary {release.binary_name} from {release.download_url}"
            )
        shutil.copy2(binary_path, destination / release.binary_name)


def download_to_path(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response:  # noqa: S310
        destination.write_bytes(response.read())


def extract_archive(archive_path: Path, extraction_root: Path, kind: str) -> None:
    if kind == "tar.gz":
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(extraction_root)  # noqa: S202
        return
    if kind == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extraction_root)
        return
    raise SystemExit(f"unsupported archive kind: {kind}")


def locate_extracted_binary(root: Path, binary_name: str) -> Path | None:
    for path in root.rglob(binary_name):
        if path.is_file():
            return path
    return None


if __name__ == "__main__":
    raise SystemExit(main())
