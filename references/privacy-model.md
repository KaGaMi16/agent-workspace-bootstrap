# Privacy model

The public boundary is based on separation, not on trying to rewrite private history.

## Classifications

- `PORTABLE`: generic Skills, instructions, settings, and hooks that pass all checks.
- `PRIVATE_LEAVE_IN_PLACE`: authentication files and private memory. Record path metadata only; never read or copy the body.
- `GENERATED`: caches, bytecode, build output, and VCS metadata. Ignore.
- `MANUAL_REVIEW`: known host assets whose format or source-of-truth status is not supported. Leave untouched.
- `CONFLICT` / `UNSAFE`: ambiguous or dangerous input. Block all live changes.

## Content blockers

The bundled scanner rejects common private keys, service tokens, assigned credential values, non-example email addresses, phone-like personal contacts, and machine-specific absolute home paths. It reports only labels and relative paths, never matched values.

Heuristic scanning cannot prove that arbitrary text contains no personal information. The Skill therefore also requires a human review of the path/classification plan before migration and a fresh scan of the public export before publication.

## Filesystem blockers

Portable source trees may contain regular files and directories only. Symlinks, sockets, devices, FIFOs, nested `.git`, unreadable files or directories, foreign-owned entries, group/world-writable entries, excessive file counts, and oversized files block automatic migration. The lexical parent chain from the selected home to every registered source is checked before traversal.

## Git boundary

The scaffold does not commit. `state/`, backups, credentials, caches, and transaction receipts are excluded by `.gitignore`. The Skill's own public release is created from an explicit allowlist into a new directory with no inherited `.git` history.

## Kimi bridge boundary

Kimi support is intentionally indirect. The generated bridge catalogs only `SKILL.md` manifests, four explicit shared rule documents, and Markdown persona/subagent files. Runtime configuration is excluded by an allowlist rather than inferred from filenames. The bridge rejects symlinks, special files, unsafe ownership or permissions, credential-like filenames, unsupported subagent types, Git metadata, and unknown categories. It never reads file bodies while building the catalog and never enumerates `.kimi` or `state/`.
