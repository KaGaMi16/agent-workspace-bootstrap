# Architecture

## Generated layout

```text
$AI_INFRA_HOME (default: ~/ai-infra)/
├── control/
│   ├── bin/                    # safe relink, skill index, validation
│   └── README.md
├── content/
│   ├── skills/                 # shared Skill source
│   ├── settings/
│   │   ├── claude/
│   │   ├── codex/
│   │   └── agents/
│   └── subagent/
├── state/                      # inventory, transaction receipts, private state
├── .gitignore                  # excludes state, backups, caches, credentials
├── AGENTS.md
└── README.md
```

`control/` and `content/` are portable. `state/` is local-only and created with owner-only permissions where the platform supports them.

## Link targets

```text
~/.claude/skills        -> ~/ai-infra/content/skills
~/.codex/skills         -> ~/ai-infra/content/skills
~/.agents/skills        -> ~/ai-infra/content/skills
~/.claude/CLAUDE.md     -> ~/ai-infra/content/settings/claude/CLAUDE.md
~/.claude/settings.json -> ~/ai-infra/content/settings/claude/settings.json
~/.claude/hooks         -> ~/ai-infra/content/settings/claude/hooks
~/.codex/AGENTS.md      -> ~/ai-infra/content/settings/codex/AGENTS.md
~/.codex/config.toml    -> ~/ai-infra/content/settings/codex/config.toml
~/.agents/AGENTS.md     -> ~/ai-infra/content/settings/agents/AGENTS.md
```

Absent paths may be linked to the generated placeholders. Existing supported paths are copied into staging, preserved as timestamped backups, and replaced only after the approved snapshot is revalidated.

## Transaction boundary

1. Recompute the approved inventory and reject drift.
2. Build the complete candidate in a sibling staging directory.
3. Run privacy and structural validation against staging.
4. Recheck the source snapshot and save the approved plan under owner-only `state/inventory/` with its SHA-256 in the transaction receipt.
5. Atomically rename staging into the final root.
6. Before each live change, recheck that source path. Write `PREPARED` to an fsynced journal, then move the original backup, write `BACKED_UP`, create the link, and write `LINKED`.
7. On ordinary failure, reverse every prepared operation. After a crash or power loss, `scaffold.py --recover-root <ai-infra-root>` validates the plan receipt and replays the same recovery. A failed recovery leaves `RECOVERY_REQUIRED`; doctor must fail.

The transaction never deletes backups. Removing old backups is a separate user-authorized task.
