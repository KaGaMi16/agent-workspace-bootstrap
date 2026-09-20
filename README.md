# kgm-agent-workspace-bootstrap

A Codex/Claude/Hermes Skill that builds a local agent workspace and safely consolidates existing agent assets. Kimi uses the generated bridge Skill instead of exposing its private runtime directory.

It creates:

```text
~/kgm-agent-workspace/
├── control/
├── content/
│   ├── skills/
│   ├── settings/
│   └── subagent/
└── state/          # private and excluded from Git
```

The Skill supports macOS and Linux. It fully handles registered Claude Code, Codex, Hermes, and `.agents` paths. It also generates `kgm-kimi-agent-workspace-bridge`, a read-only Kimi entry point that catalogs portable workspace content without reading `.kimi` or `state/`. Other tools are discovered conservatively and left untouched for manual review.

## Safety model

- Inventory is read-only.
- No whole-home scan.
- Secrets, private memory, symlinks, nested Git repositories, special files, and ambiguous conflicts block automatic migration.
- The exact inventory digest must be approved before applying changes.
- Every replaced original is preserved as a timestamped backup.
- The approved inventory and a write-ahead transaction journal are kept under private `state/` with owner-only permissions.
- Failed link operations are rolled back. After a process crash or power loss, `scaffold.py --recover-root <agent-workspace-root>` restores from the journal before any retry.
- No automatic Git commit, remote, push, or GitHub publication.
- Kimi credentials, history, telemetry, device identifiers, and configuration are never read or migrated.

## Install

Place this folder in a supported Skill directory, then ask your agent:

```text
Use $kgm-agent-workspace-bootstrap to inventory this machine and show me the migration plan. Do not change files until I approve the plan digest.
```

After scaffolding, a Kimi user can install the generated `content/skills/kgm-kimi-agent-workspace-bridge` through Kimi's normal Skill installation flow. The bridge lists only Skill manifests, four shared rule documents, and Markdown persona/subagent files. Runtime configuration files are excluded. It stops on symlinks, unsafe filesystem entries, credential-like names, or unsupported subagent file types.

## Development

Run the self-contained tests:

```bash
python3 -m unittest discover -s tests -v
```

Validate the Skill with the host's `skill-creator/scripts/quick_validate.py`, then use `scripts/export_public.py` to build a clean public package.

A normal Git clone is supported: repository-root Git metadata is excluded from exports. If the LICENSE is already signed, pass the same GitHub handle; the exporter refuses attribution changes.

## Legacy installations

New installs default to `~/kgm-agent-workspace`. The older `AI_INFRA_HOME` and `AI_INFRA_SOURCE_*` environment variables remain supported as deprecated aliases. Existing `~/ai-infra` directories are never adopted automatically; set `KGM_AGENT_WORKSPACE_HOME` or the legacy `AI_INFRA_HOME` explicitly only when that directory is a known installation of this Skill.

## License

MIT. The release exporter requires the owner to provide the exact public GitHub handle before generating the distributable package.
