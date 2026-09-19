#!/usr/bin/env python3
"""Read-only inventory and migration-plan generator for an agent workspace.

Exit codes: 0=DRY, 3=MIGRATE, 1=BLOCKED, 2=usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from privacy_scan import scan_path

DRY = "DRY"
MIGRATE = "MIGRATE"
BLOCKED = "BLOCKED"
EXIT_CODES = {DRY: 0, BLOCKED: 1, MIGRATE: 3}
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class AssetSpec:
    key: str
    provider: str
    relative: str
    destination: str | None
    classification: str
    kind: str = "file"


PORTABLE_SPECS = (
    AssetSpec("claude_skills", "claude", ".claude/skills", "content/skills", "PORTABLE", "skills"),
    AssetSpec("codex_skills", "codex", ".codex/skills", "content/skills", "PORTABLE", "skills"),
    AssetSpec("agents_skills", "agents", ".agents/skills", "content/skills", "PORTABLE", "skills"),
    AssetSpec("claude_md", "claude", ".claude/CLAUDE.md", "content/settings/claude/CLAUDE.md", "PORTABLE"),
    AssetSpec("claude_settings", "claude", ".claude/settings.json", "content/settings/claude/settings.json", "PORTABLE"),
    AssetSpec("claude_hooks", "claude", ".claude/hooks", "content/settings/claude/hooks", "PORTABLE", "directory"),
    AssetSpec("codex_agents", "codex", ".codex/AGENTS.md", "content/settings/codex/AGENTS.md", "PORTABLE"),
    AssetSpec("codex_config", "codex", ".codex/config.toml", "content/settings/codex/config.toml", "PORTABLE"),
    AssetSpec("agents_rules", "agents", ".agents/AGENTS.md", "content/settings/agents/AGENTS.md", "PORTABLE"),
)

PRIVATE_SPECS = (
    AssetSpec("codex_auth", "codex", ".codex/auth.json", None, "PRIVATE_LEAVE_IN_PLACE"),
    AssetSpec("claude_credentials", "claude", ".claude/.credentials.json", None, "PRIVATE_LEAVE_IN_PLACE"),
)

MANUAL_SPECS = (
    AssetSpec("claude_agents", "claude", ".claude/agents", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("codex_agents_dir", "codex", ".codex/agents", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("codex_hooks", "codex", ".codex/hooks.json", None, "MANUAL_REVIEW"),
    AssetSpec("hermes_skills", "hermes", ".hermes/skills", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("hermes_agents", "hermes", ".hermes/agents", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("hermes_soul", "hermes", ".hermes/SOUL.md", None, "MANUAL_REVIEW"),
    AssetSpec("kimi_root", "kimi", ".kimi", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("kimi_config", "kimi", ".config/kimi", None, "MANUAL_REVIEW", "directory"),
    AssetSpec("opencode_config", "opencode", ".config/opencode", None, "MANUAL_REVIEW", "directory"),
)


def resolve_root(home: Path) -> Path:
    raw = os.environ.get("KGM_AGENT_WORKSPACE_HOME") or os.environ.get("AI_INFRA_HOME")
    return Path(raw).expanduser().absolute() if raw else home / "kgm-agent-workspace"


def source_path(home: Path, spec: AssetSpec) -> Path:
    canonical_key = f"KGM_AGENT_SOURCE_{spec.key.upper()}"
    legacy_key = f"AI_INFRA_SOURCE_{spec.key.upper()}"
    raw = os.environ.get(canonical_key) or os.environ.get(legacy_key)
    return Path(raw).expanduser().absolute() if raw else home / spec.relative


def destination_for(spec: AssetSpec, root: Path) -> Path:
    if not spec.destination:
        raise ValueError(f"asset has no destination: {spec.key}")
    return root / spec.destination


def classify_root(root: Path) -> str:
    if root.is_symlink():
        return "occupied-symlink"
    if not root.exists():
        return "missing"
    if not root.is_dir():
        return "occupied-not-directory"
    try:
        return "empty" if not any(root.iterdir()) else "occupied"
    except OSError:
        return "unreadable"


def path_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    base = path if path.is_dir() else path.parent
    paths = [path] if not path.is_dir() else [path, *sorted(path.rglob("*"))]
    for item in paths:
        rel = "." if item == path else item.relative_to(base).as_posix()
        info = item.lstat()
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(str(stat.S_IFMT(info.st_mode)).encode())
        digest.update(str(info.st_mode & 0o7777).encode())
        digest.update(str(info.st_size).encode())
        if stat.S_ISREG(info.st_mode):
            digest.update(item.read_bytes())
        elif stat.S_ISLNK(info.st_mode):
            digest.update(os.readlink(item).encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def symlink_ancestor_findings(path: Path) -> list[dict[str, str]]:
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if not _exists(current):
            continue
        try:
            info = current.lstat()
        except OSError:
            return [{"label": "unreadable-ancestor", "path": str(current)}]
        if stat.S_ISLNK(info.st_mode):
            return [{"label": "symlink-ancestor", "path": str(current)}]
    return []


def parent_trust_findings(home: Path, path: Path) -> list[dict[str, str]]:
    """Check lexical parents without following a symlinked host directory."""
    home = home.absolute()
    path = path.absolute()
    for trusted_path in (home, path.parent):
        ancestor_findings = symlink_ancestor_findings(trusted_path)
        if ancestor_findings:
            return ancestor_findings
    try:
        relative = path.relative_to(home)
        nodes = [home]
        current = home
        for part in relative.parts[:-1]:
            current = current / part
            nodes.append(current)
    except ValueError:
        nodes = []
        current = path.parent
        while current != current.parent:
            nodes.append(current)
            if _exists(current):
                break
            current = current.parent

    findings: list[dict[str, str]] = []
    for node in nodes:
        if not _exists(node):
            continue
        try:
            info = node.lstat()
        except OSError:
            findings.append({"label": "unreadable-parent", "path": str(node)})
            continue
        if stat.S_ISLNK(info.st_mode):
            findings.append({"label": "symlink-parent", "path": str(node)})
            continue
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            findings.append({"label": "foreign-owner-parent", "path": str(node)})
        if info.st_mode & stat.S_IWGRP:
            findings.append({"label": "group-writable-parent", "path": str(node)})
        if info.st_mode & stat.S_IWOTH:
            findings.append({"label": "world-writable-parent", "path": str(node)})
    return findings


def survey_portable(home: Path, root: Path, spec: AssetSpec) -> dict:
    path = source_path(home, spec)
    record = {
        "key": spec.key,
        "provider": spec.provider,
        "path": str(path),
        "destination": spec.destination,
        "classification": spec.classification,
        "kind": spec.kind,
        "action": "absent",
        "fingerprint": None,
        "findings": [],
    }
    parent_findings = parent_trust_findings(home, path)
    if parent_findings:
        record["action"] = "blocked"
        record["findings"] = parent_findings
        return record
    if not _exists(path):
        return record
    if path.is_symlink():
        expected = destination_for(spec, root)
        try:
            resolved = path.resolve(strict=True)
            expected_resolved = expected.resolve(strict=True)
        except OSError:
            record["action"] = "blocked"
            record["findings"] = [{"label": "broken-or-unverifiable-symlink", "path": "."}]
            return record
        if resolved == expected_resolved:
            record["action"] = "already-linked"
            record["fingerprint"] = path_fingerprint(expected)
            return record
        record["action"] = "blocked"
        record["findings"] = [{"label": "managed-elsewhere", "path": "."}]
        return record
    if path.is_dir():
        try:
            if not any(path.iterdir()):
                return record
        except OSError:
            record["action"] = "blocked"
            record["findings"] = [{"label": "unreadable", "path": "."}]
            return record
    findings = scan_path(path)
    if findings:
        record["action"] = "blocked"
        record["findings"] = findings
        return record
    try:
        record["fingerprint"] = path_fingerprint(path)
    except OSError:
        record["action"] = "blocked"
        record["findings"] = [{"label": "snapshot-failed", "path": "."}]
        return record
    record["action"] = "migrate"
    return record


def survey_metadata(home: Path, spec: AssetSpec) -> dict:
    path = source_path(home, spec)
    return {
        "key": spec.key,
        "provider": spec.provider,
        "path": str(path),
        "destination": None,
        "classification": spec.classification,
        "kind": spec.kind,
        "action": "leave-in-place" if _exists(path) else "absent",
    }


def discover_private_memory(home: Path) -> list[dict]:
    parent = home / ".claude/projects"
    if not parent.is_dir() or parent.is_symlink():
        return []
    records = []
    for path in sorted(parent.glob("*/memory")):
        if _exists(path):
            records.append(
                {
                    "key": f"claude_project_memory_{len(records) + 1}",
                    "provider": "claude",
                    "path": str(path),
                    "destination": None,
                    "classification": "PRIVATE_LEAVE_IN_PLACE",
                    "kind": "directory",
                    "action": "leave-in-place",
                }
            )
    return records


def merge_skill_entries(records: list[dict]) -> tuple[dict, list[dict]]:
    by_name: dict[str, list[dict]] = {}
    collisions: list[dict] = []
    for record in records:
        if record["kind"] != "skills" or record["action"] != "migrate":
            continue
        source = Path(record["path"])
        for child in sorted(source.iterdir(), key=lambda p: p.name):
            if child.name in {".DS_Store", "__pycache__", ".pytest_cache"}:
                continue
            supported = child.is_dir() and (
                (child / "SKILL.md").is_file()
                or (
                    (child / "DESCRIPTION.md").is_file()
                    and any(path.is_file() for path in child.rglob("SKILL.md"))
                )
            )
            if not supported:
                collisions.append(
                    {
                        "kind": "unsupported-skill-entry",
                        "name": child.name,
                        "paths": [str(child)],
                    }
                )
                continue
            try:
                fingerprint = path_fingerprint(child)
            except OSError:
                collisions.append({"kind": "snapshot-failed", "name": child.name, "paths": [str(child)]})
                continue
            by_name.setdefault(child.name, []).append(
                {"source": str(child), "fingerprint": fingerprint, "provider": record["provider"]}
            )

    folded: dict[str, str] = {}
    casefolded: dict[str, set[str]] = {}
    for name in by_name:
        folded_name = unicodedata.normalize("NFC", name).casefold()
        casefolded.setdefault(folded_name, set()).add(name)
    for names in casefolded.values():
        if len(names) > 1:
            paths = [entry["source"] for name in sorted(names) for entry in by_name[name]]
            collisions.append({"kind": "case-or-unicode-name-conflict", "name": sorted(names), "paths": paths})

    for name, entries in sorted(by_name.items()):
        fingerprints = {entry["fingerprint"] for entry in entries}
        if len(fingerprints) > 1:
            collisions.append(
                {"kind": "different-content", "name": name, "paths": [entry["source"] for entry in entries]}
            )
            continue
        folded[name] = entries[0]["source"]
    return folded, collisions


def canonical_digest(report: dict) -> str:
    payload = {key: value for key, value in report.items() if key != "plan_digest"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_report(home: Path) -> dict:
    home = Path(home).expanduser().absolute()
    root = resolve_root(home)
    root_status = classify_root(root)
    blockers: list[dict] = []
    system = platform.system()
    home_ancestor_findings = symlink_ancestor_findings(home)
    if home_ancestor_findings:
        blockers.append(
            {"kind": "untrusted-home", "path": str(home), "findings": home_ancestor_findings}
        )
    if system not in {"Darwin", "Linux"}:
        blockers.append({"kind": "unsupported-platform", "detail": system})
    if root_status not in {"missing", "empty"}:
        blockers.append({"kind": "root-occupied", "detail": root_status})
    root_findings = parent_trust_findings(home, root)
    if root_status == "empty":
        root_findings.extend(scan_path(root))
    if root_findings:
        blockers.append({"kind": "untrusted-root", "path": str(root), "findings": root_findings})

    portable = [survey_portable(home, root, spec) for spec in PORTABLE_SPECS]
    private = [survey_metadata(home, spec) for spec in PRIVATE_SPECS]
    private.extend(discover_private_memory(home))
    manual = [survey_metadata(home, spec) for spec in MANUAL_SPECS]

    for record in portable:
        if record["action"] == "blocked":
            blockers.append(
                {
                    "kind": "unsafe-portable-asset",
                    "asset": record["key"],
                    "path": record["path"],
                    "findings": record["findings"],
                }
            )

    skill_entries, conflicts = merge_skill_entries(portable)
    for conflict in conflicts:
        blockers.append(
            {
                "kind": "skill-entry-conflict",
                "conflict_kind": conflict["kind"],
                "name": conflict["name"],
                "paths": conflict["paths"],
            }
        )

    migrate_count = sum(record["action"] == "migrate" for record in portable)
    verdict = BLOCKED if blockers else (MIGRATE if migrate_count else DRY)
    report = {
        "schema_version": SCHEMA_VERSION,
        "home": str(home),
        "ai_infra_root": str(root),
        "platform": system,
        "root_status": root_status,
        "verdict": verdict,
        "portable_assets": portable,
        "private_assets": private,
        "manual_review_assets": manual,
        "skill_entries": skill_entries,
        "blockers": blockers,
    }
    report["plan_digest"] = canonical_digest(report)
    return report


def print_report(report: dict) -> None:
    print(f"agent workspace root: {report['ai_infra_root']} -> {report['root_status']}")
    for group, title in (
        (report["portable_assets"], "portable"),
        (report["private_assets"], "private (leave in place)"),
        (report["manual_review_assets"], "manual review (leave in place)"),
    ):
        visible = [item for item in group if item["action"] != "absent"]
        if not visible:
            continue
        print(f"\n{title} assets:")
        for item in visible:
            print(f"  {item['key']}: {item['path']} -> {item['action']}")
            for finding in item.get("findings", []):
                print(f"    BLOCKED {finding['label']}: {finding['path']}")
    if report["skill_entries"]:
        print(f"\nskill-root entries to merge ({len(report['skill_entries'])}):")
        for name in sorted(report["skill_entries"]):
            print(f"  - {name}")
    if report["blockers"]:
        print("\nblockers:")
        for blocker in report["blockers"]:
            print(f"  - {blocker['kind']}: {blocker.get('asset') or blocker.get('name') or blocker.get('detail')}")
    print(f"\nplan_digest: {report['plan_digest']}")
    print(f"RESULT: {report['verdict']}")
    if report["verdict"] == BLOCKED:
        print("Nothing was written. Resolve every blocker and run preflight again.")
    else:
        print("Review this exact plan. Save --json output and approve its digest before scaffold.py.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="treat this directory as HOME (tests and controlled installs)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    raw_home = Path(args.home).expanduser() if args.home else Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    if raw_home.is_symlink():
        print("BLOCKED: HOME itself is a symlink; refuse ambiguous path trust")
        return 1
    report = build_report(raw_home.absolute())
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return EXIT_CODES[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
