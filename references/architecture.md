# Architecture

## Generated layout

```text
$KGM_AGENT_WORKSPACE_HOME (default: ~/kgm-agent-workspace)/
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
~/.claude/skills        -> ~/kgm-agent-workspace/content/skills
~/.codex/skills         -> ~/kgm-agent-workspace/content/skills
~/.agents/skills        -> ~/kgm-agent-workspace/content/skills
~/.claude/CLAUDE.md     -> ~/kgm-agent-workspace/content/settings/claude/CLAUDE.md
~/.claude/settings.json -> ~/kgm-agent-workspace/content/settings/claude/settings.json
~/.claude/hooks         -> ~/kgm-agent-workspace/content/settings/claude/hooks
~/.codex/AGENTS.md      -> ~/kgm-agent-workspace/content/settings/codex/AGENTS.md
~/.codex/config.toml    -> ~/kgm-agent-workspace/content/settings/codex/config.toml
~/.agents/AGENTS.md     -> ~/kgm-agent-workspace/content/settings/agents/AGENTS.md
```

Absent paths may be linked to the generated placeholders. Existing supported paths are copied into staging, preserved as timestamped backups, and replaced only after the approved snapshot is revalidated.

## Transaction boundary

1. Recompute the approved inventory and reject drift.
2. Build the complete candidate in a sibling staging directory.
3. Run privacy and structural validation against staging.
4. Recheck the source snapshot and save the approved plan under owner-only `state/inventory/` with its SHA-256 in the transaction receipt.
5. Atomically rename staging into the final root.
6. Before each live change, recheck that source path. Write `PREPARED` to an fsynced journal, then move the original backup, write `BACKED_UP`, create the link, and write `LINKED`.
7. On ordinary failure, reverse every prepared operation. After a crash or power loss, `scaffold.py --recover-root <agent-workspace-root>` validates the plan receipt and replays the same recovery. A failed recovery leaves `RECOVERY_REQUIRED`; doctor must fail.

## Compatibility

`KGM_AGENT_WORKSPACE_HOME` is the canonical root override. `AI_INFRA_HOME` remains a deprecated alias for known legacy installations. For registered source overrides, `KGM_AGENT_SOURCE_<KEY>` takes precedence over the deprecated `AI_INFRA_SOURCE_<KEY>`. The default never auto-adopts `~/ai-infra`.

The approved-plan JSON retains the internal `ai_infra_root` key so existing receipts and crash-recovery journals remain readable. This compatibility key is not the product name.

The transaction never deletes backups. Removing old backups is a separate user-authorized task.
