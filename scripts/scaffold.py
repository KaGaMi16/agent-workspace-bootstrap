#!/usr/bin/env python3
"""Apply one explicitly approved ai-infra preflight plan.

No commit, remote, push, publication, credential migration, or deletion occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preflight import (  # noqa: E402
    BLOCKED,
    PORTABLE_SPECS,
    SCHEMA_VERSION,
    build_report,
    canonical_digest,
    destination_for,
    parent_trust_findings,
    path_fingerprint,
    symlink_ancestor_findings,
)
from privacy_scan import scan_path  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
SKELETON = SCRIPT_DIR.parent / "assets" / "skeleton"
EXECUTABLE_SUFFIXES = {".sh", ".py"}


class ApplyError(RuntimeError):
    pass


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplyError(f"cannot load plan: {exc}") from exc
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ApplyError("plan schema mismatch")
    digest = plan.get("plan_digest")
    if not isinstance(digest, str) or canonical_digest(plan) != digest:
        raise ApplyError("plan digest does not match plan content")
    return plan


def deployed_relative(rel: Path) -> Path:
    if not rel.parts:
        return rel
    first, *rest = rel.parts
    if first == "bin":
        return Path("control", "bin", *rest)
    if first in {"skills", "settings", "subagent"}:
        return Path("content", first, *rest)
    name = rel.name[: -len(".tmpl")] if rel.name.endswith(".tmpl") else rel.name
    return rel.parent / name.replace("__", os.sep)


def deploy_skeleton(root: Path, prefix: str) -> list[Path]:
    created: list[Path] = []
    for src in sorted(SKELETON.rglob("*")):
        rel = src.relative_to(SKELETON)
        mapped = deployed_relative(rel)
        if rel.name.endswith(".tmpl") and mapped.name.endswith(".tmpl"):
            mapped = mapped.with_name(mapped.name[: -len(".tmpl")])
        dest = root / mapped
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel.name.endswith(".tmpl"):
            text = src.read_text(encoding="utf-8").replace("{{SELF_AUTHORED_PREFIX}}", prefix)
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(src, dest)
        if dest.suffix in EXECUTABLE_SUFFIXES:
            dest.chmod(dest.stat().st_mode | 0o111)
        created.append(dest)
    return created


def remove_staging_target(path: Path, staging: Path) -> None:
    try:
        path.relative_to(staging)
    except ValueError as exc:
        raise ApplyError(f"refuse to replace path outside staging: {path}") from exc
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_into_staging(source: Path, destination: Path, staging: Path) -> None:
    remove_staging_target(destination, staging)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        shutil.copy2(source, destination)


def import_assets(plan: dict, staging: Path) -> None:
    skills_root = staging / "content/skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for name, source_text in sorted(plan["skill_entries"].items()):
        source = Path(source_text)
        before = path_fingerprint(source)
        destination = skills_root / name
        copy_into_staging(source, destination, staging)
        if path_fingerprint(source) != before or path_fingerprint(destination) != before:
            raise ApplyError(f"skill source changed while copying: {name}")

    spec_by_key = {spec.key: spec for spec in PORTABLE_SPECS}
    for record in plan["portable_assets"]:
        if record["action"] != "migrate" or record["kind"] == "skills":
            continue
        spec = spec_by_key[record["key"]]
        source = Path(record["path"])
        expected = record["fingerprint"]
        if path_fingerprint(source) != expected:
            raise ApplyError(f"source changed before copy: {record['key']}")
        destination = destination_for(spec, staging)
        copy_into_staging(source, destination, staging)
        if path_fingerprint(source) != expected or path_fingerprint(destination) != expected:
            raise ApplyError(f"source changed while copying: {record['key']}")


def run_validation(staging: Path) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    rebuild = subprocess.run(
        [sys.executable, "-B", str(staging / "control/bin/rebuild-claude-md.py")],
        cwd=staging,
        env=env,
        capture_output=True,
        text=True,
    )
    if rebuild.stdout.strip():
        print(rebuild.stdout.rstrip())
    if rebuild.stderr.strip():
        print(rebuild.stderr.rstrip(), file=sys.stderr)
    if rebuild.returncode != 0:
        raise ApplyError("skill index generation failed")
    findings = scan_path(staging)
    if findings:
        labels = ", ".join(sorted({item["label"] for item in findings}))
        raise ApplyError(f"staging privacy/structure scan failed: {labels}")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_journal(path: Path, journal: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(journal, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def write_inventory(path: Path, plan: dict) -> str:
    encoded = (json.dumps(plan, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(path.parent)
    return hashlib.sha256(encoded).hexdigest()


def backup_name(original: Path, stamp: str) -> Path:
    candidate = original.with_name(f"{original.name}.backup.{stamp}")
    index = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = original.with_name(f"{original.name}.backup.{stamp}.{index}")
        index += 1
    return candidate


def verify_live_record(plan: dict, record: dict) -> None:
    original = Path(record["path"])
    findings = parent_trust_findings(Path(plan["home"]), original)
    if findings:
        labels = ", ".join(sorted({item["label"] for item in findings}))
        raise ApplyError(f"untrusted live path before link: {record['key']} ({labels})")
    exists = original.exists() or original.is_symlink()
    if record["action"] == "absent":
        if exists:
            raise ApplyError(f"previously absent path appeared: {record['key']}")
        return
    if record["action"] == "migrate":
        if not exists or original.is_symlink():
            raise ApplyError(f"migrated source changed type: {record['key']}")
        if path_fingerprint(original) != record["fingerprint"]:
            raise ApplyError(f"migrated source changed before link: {record['key']}")


def rollback_operations(journal: dict, journal_path: Path) -> str:
    errors: list[dict[str, str]] = []
    for operation in reversed(journal.get("operations", [])):
        original = Path(operation["original"])
        target = Path(operation["target"])
        backup = Path(operation["backup"]) if operation.get("backup") else None
        try:
            if original.is_symlink():
                if os.readlink(original) != str(target):
                    raise ApplyError("live path became an unexpected symlink")
                original.unlink()
                fsync_directory(original.parent)
            elif original.exists():
                if backup and (backup.exists() or backup.is_symlink()):
                    raise ApplyError("live path and backup both exist")
            if backup and (backup.exists() or backup.is_symlink()):
                if original.exists() or original.is_symlink():
                    raise ApplyError("refuse to overwrite live path during recovery")
                backup.rename(original)
                fsync_directory(original.parent)
            elif operation.get("original_existed") and not original.exists() and not original.is_symlink():
                raise ApplyError("original and planned backup are both missing")
            operation["state"] = "RESTORED"
        except Exception as exc:
            errors.append({"key": operation.get("key", "unknown"), "error": type(exc).__name__})
    journal["rollback_errors"] = errors
    journal["status"] = "RECOVERY_REQUIRED" if errors else "ROLLED_BACK"
    try:
        write_journal(journal_path, journal)
    except OSError:
        journal["status"] = "RECOVERY_REQUIRED"
    return journal["status"]


def link_registered_paths(
    plan: dict,
    root: Path,
    journal_path: Path,
    inventory_relative: str,
    inventory_sha256: str,
    fail_after: int | None = None,
    fail_final_journal: bool = False,
    crash_after_backup: int | None = None,
) -> dict:
    stamp = time.strftime("%Y%m%d%H%M%S")
    spec_by_key = {spec.key: spec for spec in PORTABLE_SPECS}
    journal = {
        "schema_version": 1,
        "status": "APPLYING",
        "plan_digest": plan["plan_digest"],
        "inventory_path": inventory_relative,
        "inventory_sha256": inventory_sha256,
        "operations": [],
        "rollback_errors": [],
    }
    try:
        write_journal(journal_path, journal)
        for record in plan["portable_assets"]:
            if record["action"] == "already-linked":
                continue
            verify_live_record(plan, record)
            spec = spec_by_key[record["key"]]
            original = Path(record["path"])
            target = destination_for(spec, root)
            target.parent.mkdir(parents=True, exist_ok=True)
            original.parent.mkdir(parents=True, exist_ok=True)
            original_existed = original.exists() or original.is_symlink()
            backup = backup_name(original, stamp) if original_existed else None
            operation = {
                "key": spec.key,
                "original": str(original),
                "target": str(target),
                "backup": str(backup) if backup else None,
                "original_existed": original_existed,
                "state": "PREPARED",
            }
            journal["operations"].append(operation)
            write_journal(journal_path, journal)
            if original_existed:
                original.rename(backup)
                fsync_directory(original.parent)
                if crash_after_backup is not None and len(journal["operations"]) >= crash_after_backup:
                    os._exit(88)
            operation["state"] = "BACKED_UP"
            write_journal(journal_path, journal)
            original.symlink_to(target, target_is_directory=target.is_dir())
            fsync_directory(original.parent)
            operation["state"] = "LINKED"
            write_journal(journal_path, journal)
            if fail_after is not None and len(journal["operations"]) >= fail_after:
                raise ApplyError("injected failure for rollback test")
        if fail_final_journal:
            raise ApplyError("injected final journal failure for rollback test")
        journal["status"] = "COMMITTED"
        write_journal(journal_path, journal)
    except Exception as exc:
        journal["error"] = type(exc).__name__
        status = rollback_operations(journal, journal_path)
        raise ApplyError(f"link transaction failed; status={status}") from exc
    return journal


def recover_transaction(root: Path) -> tuple[str, Path | None]:
    root = root.expanduser().absolute()
    ancestor_findings = symlink_ancestor_findings(root)
    if ancestor_findings:
        raise ApplyError("recovery root has a symlinked or unreadable ancestor")
    journal_path = root / "state/transaction.json"
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        inventory_relative = Path(journal["inventory_path"])
        inventory_path = (root / inventory_relative).resolve(strict=True)
        inventory_path.relative_to((root / "state").resolve(strict=True))
        inventory_bytes = inventory_path.read_bytes()
        if hashlib.sha256(inventory_bytes).hexdigest() != journal["inventory_sha256"]:
            raise ApplyError("inventory receipt digest mismatch")
        plan = json.loads(inventory_bytes)
        if canonical_digest(plan) != plan.get("plan_digest"):
            raise ApplyError("inventory plan digest mismatch")
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ApplyError(f"cannot validate recovery receipts: {type(exc).__name__}") from exc

    if Path(plan["ai_infra_root"]).absolute() != root:
        raise ApplyError("recovery root differs from approved inventory")
    for record in plan["portable_assets"]:
        findings = parent_trust_findings(Path(plan["home"]), Path(record["path"]))
        if findings:
            raise ApplyError(f"recovery path is no longer trusted: {record['key']}")

    spec_by_key = {spec.key: spec for spec in PORTABLE_SPECS}
    allowed = {
        record["key"]: (
            record["path"],
            str(destination_for(spec_by_key[record["key"]], root)),
        )
        for record in plan["portable_assets"]
        if record["action"] != "already-linked"
    }
    for operation in journal.get("operations", []):
        if operation.get("key") not in allowed:
            raise ApplyError("journal contains an unapproved operation")
        if (operation.get("original"), operation.get("target")) != allowed[operation["key"]]:
            raise ApplyError("journal operation differs from approved inventory")
    if journal.get("status") == "COMMITTED":
        raise ApplyError("committed transaction does not need recovery")
    status = rollback_operations(journal, journal_path)
    failed = None
    if status == "ROLLED_BACK":
        failed = root.with_name(f".{root.name}.failed.{time.strftime('%Y%m%d%H%M%S')}")
        root.rename(failed)
    return status, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-file", type=Path)
    mode.add_argument("--recover-root", type=Path)
    parser.add_argument("--approve-digest")
    parser.add_argument("--prefix", default="my")
    parser.add_argument("--fail-after-link", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--fail-final-journal", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--crash-after-backup", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    staging: Path | None = None
    root: Path | None = None
    try:
        if args.recover_root:
            status, failed = recover_transaction(args.recover_root)
            print(f"RECOVERY: {status}")
            if failed:
                print(f"failed candidate preserved at {failed}")
            return 0 if status == "ROLLED_BACK" else 1
        if not args.approve_digest:
            raise ApplyError("--approve-digest is required with --plan-file")
        plan = load_plan(args.plan_file)
        if args.approve_digest != plan["plan_digest"]:
            raise ApplyError("approval digest does not match plan")
        if plan["verdict"] == BLOCKED:
            raise ApplyError("approved plan is BLOCKED")
        current = build_report(Path(plan["home"]))
        if current["plan_digest"] != plan["plan_digest"]:
            raise ApplyError("snapshot changed after approval; rerun preflight")

        root = Path(plan["ai_infra_root"])
        root_findings = parent_trust_findings(Path(plan["home"]), root)
        if root_findings:
            labels = ", ".join(sorted({item["label"] for item in root_findings}))
            raise ApplyError(f"untrusted ai-infra root parent: {labels}")
        staging = root.with_name(f".{root.name}.staging.{plan['plan_digest'][:12]}")
        if staging.exists() or staging.is_symlink():
            raise ApplyError(f"staging path already exists: {staging}")
        staging.mkdir(parents=True, mode=0o700)
        deploy_skeleton(staging, args.prefix)
        import_assets(plan, staging)
        state = staging / "state"
        state.mkdir(mode=0o700)
        (staging / "content/subagent/imported").mkdir(parents=True, exist_ok=True)
        run_validation(staging)
        current = build_report(Path(plan["home"]))
        if current["plan_digest"] != plan["plan_digest"]:
            raise ApplyError("snapshot changed while staging; rerun preflight")
        inventory_relative = Path("state/inventory") / f"{plan['plan_digest']}.json"
        inventory_sha256 = write_inventory(staging / inventory_relative, plan)

        if root.exists():
            if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
                raise ApplyError("target root changed or is no longer empty")
            root.rmdir()
        staging.rename(root)
        fsync_directory(root.parent)
        staging = None

        journal_path = root / "state/transaction.json"
        try:
            journal = link_registered_paths(
                plan,
                root,
                journal_path,
                inventory_relative.as_posix(),
                inventory_sha256,
                args.fail_after_link,
                args.fail_final_journal,
                args.crash_after_backup,
            )
        except ApplyError:
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ApplyError("link transaction failed; status=RECOVERY_REQUIRED")
            if journal.get("status") == "ROLLED_BACK":
                failed = root.with_name(f".{root.name}.failed.{time.strftime('%Y%m%d%H%M%S')}")
                root.rename(failed)
                print(f"ROLLED_BACK: live paths restored; failed candidate preserved at {failed}", file=sys.stderr)
            raise

        backups = [item["backup"] for item in journal["operations"] if item["backup"]]
        print(f"DONE: ai-infra installed at {root}")
        print(f"transaction: {journal['status']} ({len(journal['operations'])} link operation(s))")
        print(f"backups preserved: {len(backups)}")
        print("No Git commit, remote, push, or publication was performed.")
        return 0
    except ApplyError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    finally:
        if staging is not None and staging.exists():
            print(f"staging preserved for inspection: {staging}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
