#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
exec python3 -B "$root/control/bin/skill_inventory.py" validate --root "$root/content/skills"
