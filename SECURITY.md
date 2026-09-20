# Security Policy

Do not include real credentials, private memory, personal contact information, or machine-specific private paths in a report.

Report a suspected vulnerability through a private GitHub Security Advisory on the repository. Do not open a public issue containing secrets or personal data.

Supported releases are the latest published release only.

Hermes sources are subject to the same secret, ownership, permission, symlink, and rollback checks as Claude and Codex sources. The generated Kimi bridge is read-only: it may catalog Skill manifests, the allowlisted shared rule documents, and Markdown persona/subagent files below `content/`, but must never enumerate `.kimi`, `state/`, runtime configuration, credentials, history, telemetry, device identifiers, receipts, or backups.
