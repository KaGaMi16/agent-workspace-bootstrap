#!/usr/bin/env python3
"""Read-only verification for a scaffolded ai-infra v2 tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preflight import PORTABLE_SPECS, destination_for, resolve_root, source_path  # noqa: E402
from privacy_scan import scan_path  # noqa: E402


class Results:
    def __init__(self) -> None:
        self.values: list[bool] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        line = f"{'PASS' if ok else 'FAIL'}: {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        self.values.append(ok)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="treat this directory as HOME")
    args = parser.parse_args()
    home = Path(args.home).expanduser().absolute() if args.home else Path(os.environ.get("HOME", str(Path.home()))).expanduser().absolute()
    root = resolve_root(home)
    results = Results()

    results.check("ai-infra root exists", root.is_dir() and not root.is_symlink(), str(root))
    required = [
        "control/bin/link-local.sh",
        "control/bin/new-skill.sh",
        "control/bin/rebuild-claude-md.py",
        "control/bin/skill_inventory.py",
        "control/bin/validate-skills.sh",
        "content/skills",
        "content/settings/claude/CLAUDE.md",
        "content/settings/claude/settings.json",
        "content/settings/claude/hooks",
        "content/settings/codex/AGENTS.md",
        "content/settings/codex/config.toml",
        "content/settings/agents/AGENTS.md",
        "content/subagent",
        "state",
        ".gitignore",
        "AGENTS.md",
        "README.md",
    ]
    for rel in required:
        path = root / rel
        results.check(f"required path present: {rel}", path.exists() and not path.is_symlink(), str(path))

    state = root / "state"
    if state.is_dir():
        mode = stat.S_IMODE(state.stat().st_mode)
        results.check("state is owner-only", mode & 0o077 == 0, oct(mode))
    ignore = root / ".gitignore"
    ignore_text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    for token in (
        "/state/",
        "*.backup.*",
        "__pycache__/",
        "*.pyc",
        ".env",
        "auth.json",
        "credentials.json",
        ".credentials.json",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.kdbx",
    ):
        results.check(f"Git ignore covers {token}", token in ignore_text)

    for spec in PORTABLE_SPECS:
        live = source_path(home, spec)
        expected = destination_for(spec, root)
        ok = live.is_symlink()
        resolved = None
        if ok:
            try:
                resolved = live.resolve(strict=True)
                ok = resolved == expected.resolve(strict=True)
            except OSError:
                ok = False
        results.check(f"registered link correct: {spec.key}", ok, f"{live} -> {resolved or '(invalid)'}")

    journal_path = root / "state/transaction.json"
    if journal_path.is_file():
        journal: dict = {}
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            status = journal.get("status")
        except (OSError, json.JSONDecodeError):
            status = "INVALID"
        results.check("transaction committed", status == "COMMITTED", str(status))
        try:
            inventory_path = (root / journal["inventory_path"]).resolve(strict=True)
            inventory_path.relative_to((root / "state").resolve(strict=True))
            inventory_bytes = inventory_path.read_bytes()
            inventory_ok = hashlib.sha256(inventory_bytes).hexdigest() == journal["inventory_sha256"]
            inventory_mode = stat.S_IMODE(inventory_path.stat().st_mode)
        except (OSError, KeyError, ValueError):
            inventory_ok = False
            inventory_mode = 0
        results.check("approved inventory receipt matches journal", inventory_ok)
        results.check("approved inventory receipt is owner-only", inventory_mode & 0o077 == 0, oct(inventory_mode))
    else:
        results.check("transaction journal present", False, str(journal_path))

    content_findings = scan_path(root / "content") if (root / "content").exists() else []
    results.check(
        "portable content passes privacy/structure scan",
        not content_findings,
        ", ".join(sorted({item["label"] for item in content_findings})) or "0 findings",
    )

    git_dir = root / ".git"
    if git_dir.exists():
        tracked = run(["git", "ls-files", "--", "state", "*.backup.*"], cwd=root)
        results.check("Git tracks no state or backups", tracked.returncode == 0 and not tracked.stdout.strip())
        remotes = run(["git", "remote", "-v"], cwd=root)
        results.check("no Git remote configured", remotes.returncode == 0 and not remotes.stdout.strip())
    else:
        print("INFO: Git repository not initialized; this is expected unless separately approved.")

    validate = root / "control/bin/validate-skills.sh"
    if validate.is_file():
        proc = run(["bash", str(validate)], cwd=root)
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        results.check("Skill structure validation passes", proc.returncode == 0)

    passed = sum(results.values)
    total = len(results.values)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
