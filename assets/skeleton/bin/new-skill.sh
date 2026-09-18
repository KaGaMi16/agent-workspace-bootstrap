#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: control/bin/new-skill.sh <skill-name> [description]" >&2
  exit 2
fi

name="$1"
description="${2:-Use when the user needs the $name workflow.}"
case "$name" in
  *[!a-z0-9-]* | "" | -* | *--* | *-)
    echo "skill name must use lowercase letters, numbers, and single hyphens" >&2
    exit 2
    ;;
esac

root="$(cd "$(dirname "$0")/../.." && pwd)"
skill_dir="$root/content/skills/$name"
if [ -e "$skill_dir" ]; then
  echo "skill already exists: $skill_dir" >&2
  exit 1
fi
mkdir -p "$skill_dir"
printf '%s\n' '---' "name: $name" "description: \"$description\"" '---' '' "# $name" '' 'Describe the non-obvious workflow, decisions, and safety boundaries here.' > "$skill_dir/SKILL.md"
echo "created $skill_dir"

