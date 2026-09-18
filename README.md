# kgm-ai-infra-quickstart

A Codex/Claude Skill that builds a local AI infrastructure folder and safely consolidates existing agent assets.

It creates:

```text
~/ai-infra/
├── control/
├── content/
│   ├── skills/
│   ├── settings/
│   └── subagent/
└── state/          # private and excluded from Git
```

The Skill supports macOS and Linux. It fully handles registered Claude Code, Codex, and `.agents` paths. Other tools are discovered conservatively and left untouched for manual review.

## Safety model

- Inventory is read-only.
- No whole-home scan.
- Secrets, private memory, symlinks, nested Git repositories, special files, and ambiguous conflicts block automatic migration.
- The exact inventory digest must be approved before applying changes.
- Every replaced original is preserved as a timestamped backup.
- The approved inventory and a write-ahead transaction journal are kept under private `state/` with owner-only permissions.
- Failed link operations are rolled back. After a process crash or power loss, `scaffold.py --recover-root <ai-infra-root>` restores from the journal before any retry.
- No automatic Git commit, remote, push, or GitHub publication.

## Install

Place this folder in a supported Skill directory, then ask your agent:

```text
Use $kgm-ai-infra-quickstart to inventory this machine and show me the migration plan. Do not change files until I approve the plan digest.
```

## Development

Run the self-contained tests:

```bash
python3 -m unittest discover -s tests -v
```

Validate the Skill with the host's `skill-creator/scripts/quick_validate.py`, then use `scripts/export_public.py` to build a clean public package.

A normal Git clone is supported: repository-root Git metadata is excluded from exports. If the LICENSE is already signed, pass the same GitHub handle; the exporter refuses attribution changes.

## License

MIT. The release exporter requires the owner to provide the exact public GitHub handle before generating the distributable package.
