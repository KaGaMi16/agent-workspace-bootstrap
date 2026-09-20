#!/usr/bin/env python3
"""List portable agent-workspace assets for the Kimi bridge.

Only relative paths below content/{skills,settings,subagent} are emitted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

SCHEMA_VERSION = 1
ALLOWED_CATEGORIES = {
    "skills": Path("content/skills"),
    "settings": Path("content/settings"),
    "subagent": Path("content/subagent"),
}
ALLOWED_SETTING_FILES = (
    Path("content/settings/agents/AGENTS.md"),
    Path("content/settings/claude/CLAUDE.md"),
    Path("content/settings/codex/AGENTS.md"),
    Path("content/settings/hermes/SOUL.md"),
)
FORBIDDEN_NAMES = {
    ".env",
    ".git",
    "auth.json",
    "credentials.json",
    ".credentials.json",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
}
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".kdbx"}
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
SENSITIVE_TOKENS = {
    "auth",
    "authentication",
    "backup",
    "backups",
    "credential",
    "credentials",
    "history",
    "jwt",
    "oauth",
    "password",
    "passwords",
    "receipt",
    "receipts",
    "secret",
    "secrets",
    "session",
    "sessions",
    "telemetry",
    "token",
    "tokens",
    "bearer",
    "certificate",
    "certificates",
    "cookie",
    "cookies",
}
KEY_PREFIXES = {
    "access",
    "account",
    "api",
    "client",
    "encryption",
    "private",
    "secret",
    "service",
    "signing",
    "ssh",
    "user",
}
IDENTIFIER_PREFIXES = {"client", "device", "hardware", "host", "machine", "system"}
IDENTIFIER_TOKENS = {"fingerprint", "guid", "id", "identifier", "uid", "uuid"}
CREDENTIAL_PREFIXES = KEY_PREFIXES | {"auth", "bearer", "refresh", "serviceaccount"}
CREDENTIAL_SUFFIXES = {"certificate", "credential", "key", "secret", "token"}
PRIVATE_DATA_PREFIXES = {"backup", "credential", "history", "receipt", "session", "telemetry"}
PRIVATE_DATA_SUFFIXES = {"archive", "cache", "data", "file", "log", "record", "store"}
SENSITIVE_COMPOUNDS = {
    f"{prefix}{suffix}"
    for prefix in CREDENTIAL_PREFIXES
    for suffix in CREDENTIAL_SUFFIXES
} | {
    f"{prefix}{suffix}"
    for prefix in PRIVATE_DATA_PREFIXES
    for suffix in PRIVATE_DATA_SUFFIXES
}


class CatalogError(ValueError):
    """The workspace cannot be cataloged without crossing a safety boundary."""


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "(outside-workspace)"


def _check_node(path: Path, root: Path, *, directory: bool) -> os.stat_result:
    relative = _relative(path, root)
    try:
        info = path.lstat()
    except OSError as exc:
        raise CatalogError(f"unreadable path: {relative} ({type(exc).__name__})") from exc
    if stat.S_ISLNK(info.st_mode):
        raise CatalogError(f"symlink is not allowed: {relative}")
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected:
        kind = "directory" if directory else "regular file"
        raise CatalogError(f"expected {kind}: {relative}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise CatalogError(f"foreign-owned path: {relative}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CatalogError(f"group/world-writable path: {relative}")
    required = stat.S_IRUSR | (stat.S_IXUSR if directory else 0)
    access = os.R_OK | (os.X_OK if directory else 0)
    if info.st_mode & required != required or not os.access(path, access):
        raise CatalogError(f"unreadable path: {relative}")
    return info


def _check_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current /= part
        if not os.path.lexists(current):
            continue
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise CatalogError("workspace root has a symlinked ancestor")
    _check_node(root, root, directory=True)
    _check_node(root / "content", root, directory=True)
    return root


def _check_relative_file(root: Path, relative: Path) -> Path:
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        _check_node(current, root, directory=index < len(relative.parts) - 1)
    return current


def _forbidden_name(name: str) -> bool:
    original_lower = name.casefold()
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    expanded = re.sub(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", "-", expanded)
    lowered = expanded.casefold()
    tokens = {token for token in re.split(r"[^a-z0-9]+", lowered) if token}
    compound = bool(tokens & SENSITIVE_COMPOUNDS)
    key_name = "key" in tokens and bool(tokens & KEY_PREFIXES)
    service_account = {"service", "account"}.issubset(tokens) or "serviceaccount" in tokens
    connection_string = bool(tokens & {"database", "db"}) and {"connection", "string"}.issubset(tokens)
    identifier = bool(tokens & IDENTIFIER_PREFIXES) and bool(tokens & IDENTIFIER_TOKENS)
    compound_identifier = any(
        f"{prefix}{identifier_token}" in tokens
        for prefix in IDENTIFIER_PREFIXES
        for identifier_token in IDENTIFIER_TOKENS
    )
    sensitive = (
        bool(tokens & SENSITIVE_TOKENS)
        or compound
        or key_name
        or service_account
        or connection_string
        or identifier
        or compound_identifier
    )
    return (
        original_lower in FORBIDDEN_NAMES
        or Path(original_lower).suffix in FORBIDDEN_SUFFIXES
        or sensitive
    )


def _catalog_category(root: Path, category: str) -> list[dict[str, str]]:
    if category == "settings":
        entries: list[dict[str, str]] = []
        for child in ALLOWED_SETTING_FILES:
            _check_relative_file(root, child)
            entries.append({"category": category, "path": child.as_posix()})
        return entries

    base = root / ALLOWED_CATEGORIES[category]
    _check_node(base, root, directory=True)
    entries: list[dict[str, str]] = []

    def refuse_walk_error(error: OSError) -> None:
        failed = Path(error.filename) if error.filename else base
        raise CatalogError(f"unreadable path: {_relative(failed, root)} ({type(error).__name__})")

    for current_text, dirnames, filenames in os.walk(
        base,
        followlinks=False,
        onerror=refuse_walk_error,
    ):
        current = Path(current_text)
        safe_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            _check_node(child, root, directory=True)
            if name in CACHE_NAMES:
                continue
            if name == ".git" or _forbidden_name(name):
                raise CatalogError(f"forbidden directory name: {_relative(child, root)}")
            safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in sorted(filenames):
            child = current / name
            _check_node(child, root, directory=False)
            if name in CACHE_NAMES:
                continue
            if _forbidden_name(name):
                raise CatalogError(f"forbidden file name: {_relative(child, root)}")
            if name.startswith("."):
                continue
            if category == "skills" and name != "SKILL.md":
                continue
            if category == "subagent" and child.suffix.casefold() != ".md":
                raise CatalogError(f"unsupported subagent file type: {_relative(child, root)}")
            entries.append({"category": category, "path": _relative(child, root)})
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("KGM_AGENT_WORKSPACE_HOME", Path.home() / "kgm-agent-workspace")),
        help="owner-approved KGM agent workspace root",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=tuple(ALLOWED_CATEGORIES),
        help="limit output to one or more portable categories",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    categories = args.category or list(ALLOWED_CATEGORIES)
    try:
        root = _check_root(args.root)
        entries = [
            entry
            for category in categories
            for entry in _catalog_category(root, category)
        ]
    except (CatalogError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    payload = {"schema_version": SCHEMA_VERSION, "entries": entries}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for entry in entries:
            print(f"{entry['category']}\t{entry['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
