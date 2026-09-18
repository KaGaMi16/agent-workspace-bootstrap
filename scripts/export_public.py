#!/usr/bin/env python3
"""Create a clean, allowlisted public export of this Skill.

This tool does not initialize Git or contact GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from preflight import path_fingerprint
from privacy_scan import scan_path

ALLOWED_TOP_LEVEL = {
    "SKILL.md",
    "README.md",
    "LICENSE",
    "SECURITY.md",
    "agents",
    "assets",
    "references",
    "scripts",
    "tests",
}
HANDLE_RE = re.compile(r"^(?!-)(?!.*--)[A-Za-z0-9-]{1,39}(?<!-)$")
PLACEHOLDER = "<YOUR_" + "GITHUB_HANDLE>"


class ExportError(RuntimeError):
    pass


def validate_output_parent(path: Path) -> None:
    lexical = path.absolute()
    current = Path(lexical.anchor)
    for part in lexical.parts[1:-1]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if stat.S_ISLNK(current.lstat().st_mode):
            raise ExportError(f"output ancestor is a symlink: {current}")
    current = path.parent
    while not current.exists() and not current.is_symlink():
        if current == current.parent:
            raise ExportError("cannot find an existing output parent")
        current = current.parent
    info = current.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ExportError(f"output parent is a symlink: {current}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ExportError(f"output parent has a different owner: {current}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ExportError(f"output parent is group/world writable: {current}")


def validate_source(source: Path) -> None:
    actual = {path.name for path in source.iterdir() if path.name not in {"__pycache__", ".DS_Store", ".git"}}
    unexpected = sorted(actual - ALLOWED_TOP_LEVEL)
    missing = sorted({"SKILL.md", "README.md", "LICENSE", "SECURITY.md"} - actual)
    git_metadata = source / ".git"
    if git_metadata.is_symlink() or (
        git_metadata.exists() and not (git_metadata.is_dir() or git_metadata.is_file())
    ):
        raise ExportError("root .git metadata must be a regular file or directory")
    if unexpected:
        raise ExportError(f"unexpected top-level paths: {', '.join(unexpected)}")
    if missing:
        raise ExportError(f"missing required paths: {', '.join(missing)}")


def scan_allowlisted_source(source: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for top_name in sorted(ALLOWED_TOP_LEVEL):
        path = source / top_name
        if path.exists() or path.is_symlink():
            findings.extend(scan_path(path))
    return findings


def allowlisted_fingerprint(source: Path) -> str:
    digest = hashlib.sha256()
    for top_name in sorted(ALLOWED_TOP_LEVEL):
        path = source / top_name
        if path.exists() or path.is_symlink():
            digest.update(top_name.encode("utf-8"))
            digest.update(path_fingerprint(path).encode("ascii"))
    return digest.hexdigest()


def copy_allowlisted(source: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ExportError(f"output must not already exist: {output}")
    output.mkdir(parents=True, mode=0o755)
    for top_name in sorted(ALLOWED_TOP_LEVEL):
        src = source / top_name
        if not src.exists():
            continue
        dest = output / top_name
        if src.is_symlink():
            raise ExportError(f"symlink not allowed: {top_name}")
        if src.is_dir():
            shutil.copytree(
                src,
                dest,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache"),
            )
        elif src.is_file():
            shutil.copy2(src, dest)
        else:
            raise ExportError(f"special file not allowed: {top_name}")


def replace_handle(output: Path, handle: str) -> None:
    if not HANDLE_RE.fullmatch(handle):
        raise ExportError("GitHub handle must be 1-39 letters, digits, or single hyphens")
    license_path = output / "LICENSE"
    text = license_path.read_text(encoding="utf-8")
    if PLACEHOLDER in text:
        license_path.write_text(text.replace(PLACEHOLDER, handle), encoding="utf-8")
    elif f"Copyright (c) 2026 {handle}" not in text:
        raise ExportError("LICENSE placeholder missing or existing handle differs")
    for path in output.rglob("*"):
        if path.is_file() and path.suffix not in {".pyc"}:
            try:
                body = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if PLACEHOLDER in body:
                raise ExportError(f"unfinished GitHub handle placeholder: {path.relative_to(output)}")


def create_deterministic_zip(source: Path, archive: Path) -> str:
    if archive.exists() or archive.is_symlink():
        raise ExportError(f"archive must not already exist: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            rel = Path(source.name) / path.relative_to(source)
            info = zipfile.ZipInfo(rel.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            mode = stat.S_IMODE(path.stat().st_mode)
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-handle", required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.expanduser().absolute()
    try:
        source_resolved = source.resolve()
        output_resolved = output.resolve(strict=False)
        if output_resolved == source_resolved or output_resolved.is_relative_to(source_resolved):
            raise ExportError("output must be outside the source package")
        archive = args.archive.expanduser().absolute() if args.archive else None
        if archive:
            archive_resolved = archive.resolve(strict=False)
            if archive_resolved == source_resolved or archive_resolved.is_relative_to(source_resolved):
                raise ExportError("archive must be outside the source package")
            if archive_resolved == output_resolved or archive_resolved.is_relative_to(output_resolved):
                raise ExportError("archive must not be inside the exported directory")
        validate_output_parent(output)
        if archive:
            validate_output_parent(archive)
        validate_source(source)
        source_findings = scan_allowlisted_source(source)
        if source_findings:
            labels = ", ".join(sorted({item["label"] for item in source_findings}))
            raise ExportError(f"source package scan failed: {labels}")
        source_digest = allowlisted_fingerprint(source)
        if output.exists() or output.is_symlink():
            raise ExportError(f"output must not already exist: {output}")
        if archive:
            if archive.exists() or archive.is_symlink():
                raise ExportError(f"archive must not already exist: {archive}")
        output.parent.mkdir(parents=True, exist_ok=True)
        archive_sha = None
        with tempfile.TemporaryDirectory(prefix=f".{output.name}.staging-", dir=output.parent) as raw_staging:
            candidate = Path(raw_staging) / output.name
            copy_allowlisted(source, candidate)
            replace_handle(candidate, args.github_handle)
            findings = scan_path(candidate)
            if findings:
                labels = ", ".join(sorted({item["label"] for item in findings}))
                raise ExportError(f"public export scan failed: {labels}")
            if allowlisted_fingerprint(source) != source_digest:
                raise ExportError("source package changed during export")
            staged_archive = None
            if archive:
                staged_archive = Path(raw_staging) / "release.zip"
                archive_sha = create_deterministic_zip(candidate, staged_archive)
            if output.exists() or output.is_symlink():
                raise ExportError(f"output appeared during export: {output}")
            candidate.rename(output)
            if staged_archive:
                try:
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    if archive.exists() or archive.is_symlink():
                        raise FileExistsError(str(archive))
                    staged_archive.rename(archive)
                except OSError as exc:
                    output.rename(candidate)
                    raise ExportError(f"archive placement failed: {type(exc).__name__}") from exc
        file_count = sum(1 for path in output.rglob("*") if path.is_file())
        print(f"PUBLIC EXPORT PASS: {file_count} file(s) at {output}")
        if archive_sha:
            print(f"archive_sha256: {archive_sha}")
        print("No Git repository, remote, push, or visibility change was created.")
        return 0
    except ExportError as exc:
        print(f"PUBLIC EXPORT REFUSED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
