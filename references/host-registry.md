# Host registry

The inventory is intentionally bounded. It does not crawl the whole home directory.

## Supported portable paths

| Host | Source | Destination under `~/kgm-agent-workspace` |
| --- | --- | --- |
| Claude | `~/.claude/skills` | `content/skills` |
| Claude | `~/.claude/CLAUDE.md` | `content/settings/claude/CLAUDE.md` |
| Claude | `~/.claude/settings.json` | `content/settings/claude/settings.json` |
| Claude | `~/.claude/hooks` | `content/settings/claude/hooks` |
| Codex | `~/.codex/skills` | `content/skills` |
| Codex | `~/.codex/AGENTS.md` | `content/settings/codex/AGENTS.md` |
| Codex | `~/.codex/config.toml` | `content/settings/codex/config.toml` |
| Generic | `~/.agents/skills` | `content/skills` |
| Generic | `~/.agents/AGENTS.md` | `content/settings/agents/AGENTS.md` |

## Private paths: existence only

- `~/.codex/auth.json`
- `~/.claude/.credentials.json`
- Claude project memory directories discovered directly below `~/.claude/projects/*/memory`

## Manual-review paths

- `~/.claude/agents`
- `~/.codex/agents`
- `~/.codex/hooks.json`
- `~/.hermes/skills`, `~/.hermes/agents`, `~/.hermes/SOUL.md`
- `~/.kimi`, `~/.config/kimi`, `~/.config/opencode`

These paths are reported but never moved or linked. Add a new host only after its source-of-truth behavior and round-trip tests are documented.
