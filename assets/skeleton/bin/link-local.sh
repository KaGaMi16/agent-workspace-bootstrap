#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"

targets=(
  "${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
  "${CODEX_SKILLS_DIR:-$HOME/.codex/skills}"
  "${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}"
  "${CLAUDE_MD_TARGET:-$HOME/.claude/CLAUDE.md}"
  "${CLAUDE_SETTINGS_TARGET:-$HOME/.claude/settings.json}"
  "${CLAUDE_HOOKS_TARGET:-$HOME/.claude/hooks}"
  "${CODEX_AGENTS_TARGET:-$HOME/.codex/AGENTS.md}"
  "${CODEX_CONFIG_TARGET:-$HOME/.codex/config.toml}"
  "${AGENTS_RULES_TARGET:-$HOME/.agents/AGENTS.md}"
)
sources=(
  "$root/content/skills"
  "$root/content/skills"
  "$root/content/skills"
  "$root/content/settings/claude/CLAUDE.md"
  "$root/content/settings/claude/settings.json"
  "$root/content/settings/claude/hooks"
  "$root/content/settings/codex/AGENTS.md"
  "$root/content/settings/codex/config.toml"
  "$root/content/settings/agents/AGENTS.md"
)
labels=(
  "claude skills"
  "codex skills"
  "generic skills"
  "claude instructions"
  "claude settings"
  "claude hooks"
  "codex instructions"
  "codex settings"
  "generic instructions"
)
actions=()

check_parent_chain() {
  python3 -B - "$1" "$HOME" <<'PY'
import os
import stat
import sys
from pathlib import Path

target = Path(sys.argv[1]).expanduser().absolute()
home = Path(sys.argv[2]).expanduser().absolute()
current = Path(target.anchor)
for part in target.parts[1:-1]:
    current /= part
    if not os.path.lexists(current):
        continue
    if stat.S_ISLNK(current.lstat().st_mode):
        print(f"REFUSED: ancestor is a symlink: {current}", file=sys.stderr)
        raise SystemExit(1)
try:
    relative = target.relative_to(home)
except ValueError:
    nodes = [target.parent]
else:
    nodes = [home]
    current = home
    for part in relative.parts[:-1]:
        current /= part
        nodes.append(current)
for node in nodes:
    if not os.path.lexists(node):
        continue
    info = node.lstat()
    if stat.S_ISLNK(info.st_mode):
        print(f"REFUSED: parent is a symlink: {node}", file=sys.stderr)
        raise SystemExit(1)
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        print(f"REFUSED: parent has a different owner: {node}", file=sys.stderr)
        raise SystemExit(1)
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        print(f"REFUSED: parent is group/world writable: {node}", file=sys.stderr)
        raise SystemExit(1)
PY
}

for index in "${!targets[@]}"; do
  target="${targets[$index]}"
  source="${sources[$index]}"
  label="${labels[$index]}"
  if [ ! -e "$source" ]; then
    echo "REFUSED: $label source is missing: $source" >&2
    exit 1
  fi
  check_parent_chain "$target"
  if [ -L "$target" ]; then
    if [ "$(readlink "$target")" = "$source" ]; then
      actions+=("keep")
      continue
    fi
    echo "REFUSED: $label is an unexpected symlink: $target" >&2
    exit 1
  fi
  if [ -e "$target" ]; then
    echo "REFUSED: $label exists and was not part of an approved migration: $target" >&2
    exit 1
  fi
  actions+=("create")
done

created=()
rollback_created_links() {
  local path
  for path in "${created[@]}"; do
    if [ -L "$path" ]; then
      unlink "$path"
    fi
  done
}
trap rollback_created_links ERR

for index in "${!targets[@]}"; do
  target="${targets[$index]}"
  source="${sources[$index]}"
  label="${labels[$index]}"
  if [ "${actions[$index]}" = "keep" ]; then
    echo "PASS: $label already linked"
    continue
  fi
  mkdir -p "$(dirname "$target")"
  ln -s "$source" "$target"
  created+=("$target")
  echo "PASS: $label linked: $target -> $source"
done

trap - ERR
