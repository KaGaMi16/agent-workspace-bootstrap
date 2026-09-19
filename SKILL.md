---
name: kgm-agent-workspace-bootstrap
description: "Build a local agent workspace with shared skills, settings, and subagent content while keeping credentials, memory, backups, and machine state private. Use when someone wants to bootstrap ~/kgm-agent-workspace on macOS/Linux or safely inventory and consolidate existing Claude Code, Codex, or .agents assets. Always inventory first, show the exact migration plan, require approval of its digest, preserve originals as backups, and verify the result. Do not use for GPU, cluster, model-serving, or other AI infrastructure; installing agent CLIs; scanning an entire home directory; silently resolving conflicting assets; publishing to GitHub; multi-machine sync; Windows; or copying another person's private rules/personas."
license: MIT
metadata:
  short-description: Build and consolidate a local agent workspace safely
---

# KGM Agent Workspace Bootstrap

Build one local agent workspace with a public/private boundary:

```text
~/kgm-agent-workspace/
├── control/                       # local tools
├── content/{skills,settings,subagent}/
└── state/                         # private; Git-ignored
```

Read [references/architecture.md](references/architecture.md) for the exact layout and [references/privacy-model.md](references/privacy-model.md) before migrating existing assets.

## Workflow

1. Run `scripts/preflight.py`. It is read-only and inventories only the registered paths in [references/host-registry.md](references/host-registry.md). Never scan the whole home directory.
2. Report its literal verdict, asset classifications, blockers, manual-review items, and `plan_digest`. Do not expose matched secret values.
3. If the verdict is `BLOCKED`, stop. If it is `DRY` or `MIGRATE`, show the exact plan and get approval for that digest before any live write.
4. Save the JSON report, then run `scripts/scaffold.py --plan-file <file> --approve-digest <digest>`. It must recompute the current snapshot and refuse any drift.
5. Run `scripts/doctor.py` immediately. Report every PASS/FAIL line. A failed doctor means the job is not complete. If an earlier apply was interrupted, run `scripts/scaffold.py --recover-root <agent-workspace-root>` before creating a new plan.

## Asset policy

- `PORTABLE`: may enter `content/` only after the bundled privacy and filesystem checks pass.
- `PRIVATE_LEAVE_IN_PLACE`: credentials, authentication, and private memory stay where they are; only their existence is reported.
- `MANUAL_REVIEW`: unsupported host assets stay untouched and are named in the report.
- `CONFLICT` or `UNSAFE`: block the migration. Never pick a winner or follow a symlink.
- Generated caches and VCS metadata never migrate.

The scaffold never commits, adds a Git remote, pushes, or publishes. Those are separate user-authorized actions.

## Public release

Use `scripts/export_public.py` only for this Skill's own source package. It creates a clean allowlisted export, replaces the license placeholder with a user-provided GitHub handle, rescans the export, and can create a deterministic ZIP. It does not initialize Git or contact GitHub.

The repository root's own `.git` metadata is excluded so a normal fresh clone can run the exporter. A `.git` entry anywhere inside allowlisted Skill content still blocks export. An already signed LICENSE may be exported again only with the same GitHub handle; changing the existing attribution is refused.

## Hard stops

- Any source tree or trusted parent contains a symlink, special file, nested `.git`, unreadable item, foreign-owned item, group/world-writable item, secret pattern, personal contact, or machine-specific absolute home path.
- The target root is occupied, redirected through an unexpected symlink, or outside the selected home without an explicit environment override.
- The approved plan digest differs from the current snapshot.
- A rollback cannot restore every changed source path.
- The operating system is not macOS or Linux.
