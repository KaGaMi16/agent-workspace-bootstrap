#!/usr/bin/env python3
"""Conservative privacy and filesystem scan for portable agent assets.

Reports labels and relative paths only. It never prints matched values.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path

MAX_FILES = 10_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

SENSITIVE_NAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    ".credentials.json",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}
SENSITIVE_SUFFIXES = {".pem", ".p12", ".pfx", ".key", ".kdbx"}
GENERATED_NAMES = {".DS_Store", "__pycache__", ".pytest_cache"}

PATTERNS = [
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-style-token", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b", re.I)),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.I)),
    ("slack-token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{12,}\b", re.I)),
    ("aws-access-key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    (
        "assigned-secret",
        re.compile(
            rb"(?i)[\"']?(?:api[_-]?key|client[_-]?secret|app[_-]?secret|access[_-]?token|refresh[_-]?token|password)"
            rb"[\"']?\s*[=:]\s*[\"']?(?!\$|\{|<|example|changeme|replace-me)([A-Za-z0-9_./+=-]{8,})"
        ),
    ),
    (
        "bearer-token",
        re.compile(rb"(?i)authorization\s*:\s*bearer\s+(?!example|changeme|replace-me)[A-Za-z0-9_./+=-]{12,}"),
    ),
    ("absolute-home-path", re.compile(rb"/(?:Users|home)/[^/\s\"']+/")),
    ("phone-like-contact", re.compile(rb"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")),
]
EMAIL_RE = re.compile(rb"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,}|localhost)\b", re.I)
SAFE_EMAIL_DOMAINS = {b"example.com", b"example.org", b"example.net", b"localhost"}


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return path.name


def _finding(label: str, path: Path, root: Path) -> dict[str, str]:
    return {"label": label, "path": _relative(path, root)}


def scan_bytes(data: bytes, path: Path, root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for label, pattern in PATTERNS:
        if pattern.search(data):
            findings.append(_finding(label, path, root))
    for match in EMAIL_RE.finditer(data):
        domain = match.group(1).lower()
        if domain in SAFE_EMAIL_DOMAINS or domain.endswith(b".example.com"):
            continue
        if domain == b"users.noreply.github.com":
            continue
        findings.append(_finding("email-contact", path, root))
        break
    return findings


def scan_path(root: Path) -> list[dict[str, str]]:
    root = Path(root)
    findings: list[dict[str, str]] = []
    if not root.exists() and not root.is_symlink():
        return findings
    if root.is_symlink():
        return [_finding("symlink", root, root.parent)]

    paths = [root] if root.is_file() else [root, *sorted(root.rglob("*"))]
    file_count = 0
    total_bytes = 0

    for path in paths:
        rel_root = root if root.is_dir() else root.parent
        try:
            mode = path.lstat().st_mode
        except OSError:
            findings.append(_finding("unreadable", path, rel_root))
            continue

        if path.name in GENERATED_NAMES or path.suffix == ".pyc":
            findings.append(_finding("generated-artifact", path, rel_root))
            continue
        if path.name == ".git":
            findings.append(_finding("nested-vcs", path, rel_root))
            continue
        if stat.S_ISLNK(mode):
            findings.append(_finding("symlink", path, rel_root))
            continue
        if stat.S_ISDIR(mode):
            if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
                findings.append(_finding("foreign-owner", path, rel_root))
            if mode & stat.S_IWGRP:
                findings.append(_finding("group-writable", path, rel_root))
            if mode & stat.S_IWOTH:
                findings.append(_finding("world-writable", path, rel_root))
            required = stat.S_IRUSR | stat.S_IXUSR
            if mode & required != required or not os.access(path, os.R_OK | os.X_OK):
                findings.append(_finding("unreadable", path, rel_root))
            continue
        if not stat.S_ISREG(mode):
            findings.append(_finding("special-file", path, rel_root))
            continue
        if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
            findings.append(_finding("foreign-owner", path, rel_root))
        if mode & stat.S_IWGRP:
            findings.append(_finding("group-writable", path, rel_root))
        if mode & stat.S_IWOTH:
            findings.append(_finding("world-writable", path, rel_root))
        if path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            findings.append(_finding("sensitive-file-name", path, rel_root))
            continue
        if not mode & stat.S_IRUSR or not os.access(path, os.R_OK):
            findings.append(_finding("unreadable", path, rel_root))
            continue

        size = path.stat().st_size
        file_count += 1
        total_bytes += size
        if file_count > MAX_FILES:
            findings.append(_finding("file-count-limit", path, rel_root))
            break
        if size > MAX_FILE_BYTES:
            findings.append(_finding("file-size-limit", path, rel_root))
            continue
        if total_bytes > MAX_TOTAL_BYTES:
            findings.append(_finding("total-size-limit", path, rel_root))
            break
        try:
            data = path.read_bytes()
        except OSError:
            findings.append(_finding("unreadable", path, rel_root))
            continue
        if b"\x00" in data:
            findings.append(_finding("binary-content-manual-review", path, rel_root))
            continue
        findings.extend(scan_bytes(data, path, rel_root))

    unique = {(item["label"], item["path"]): item for item in findings}
    return [unique[key] for key in sorted(unique)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    findings = scan_path(args.path.expanduser())
    if args.json:
        print(json.dumps({"path": str(args.path), "findings": findings}, indent=2))
    else:
        for item in findings:
            print(f"BLOCKED {item['label']}: {item['path']}")
        print("PRIVACY SCAN PASS" if not findings else f"PRIVACY SCAN FAIL: {len(findings)} finding(s)")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
