#!/usr/bin/env python3
"""重建 CLAUDE.md 的技能自动调用索引。

用法：python3 bin/rebuild-claude-md.py [--check]
活动范围由 ``bin/skill_inventory.py`` 统一定义：顶层 Skill，以及带
``DESCRIPTION.md`` 的分类目录中的子 Skill。普通来源包和隐藏目录不进入索引。
"""
import os
import sys
from pathlib import Path

from skill_inventory import InventoryError, discover_skills

ROOT = str(Path(__file__).resolve().parents[2])
SKILLS = os.path.join(ROOT, "content", "skills")

IN_PROGRESS = set()  # 按需在此列出尚未完工、要打〔in-progress〕标记的技能名

arguments = sys.argv[1:]
if arguments not in ([], ["--check"]):
    print("usage: rebuild-claude-md.py [--check]", file=sys.stderr)
    raise SystemExit(2)

try:
    records, warnings = discover_skills(os.path.abspath(SKILLS))
except (OSError, InventoryError) as exc:
    print(f"inventory error: {exc}", file=sys.stderr)
    raise SystemExit(1)

rows, lark = [], []
for record in records:
    name = record.name
    if name.startswith("lark-") and "/" not in record.relative_directory:
        lark.append(name[len("lark-") :])
        continue
    if not record.description:
        warnings.append(f"description 为空: {record.relative_directory}")
    tag = "〔in-progress〕" if name in IN_PROGRESS else ""
    rows.append(f"| {name} | {record.description}{tag} |")

out = """# Shared Skill Repository

This repository is the single source of truth for skills shared by Claude and Codex.

Follow the same rules in `AGENTS.md`:

- Standalone skills live in `content/skills/<skill-name>/`; managed categories may contain nested skill directories.
- Downloaded, imported, and locally created skills all live together in `content/skills/`.
- Every skill must contain `SKILL.md` with `name` and `description` frontmatter.
- Keep instructions concise and place bulky material in `references/`, scripts in `scripts/`, and reusable files in `assets/`.
- Use Git branches for risky edits and commit useful changes.

## Skill Auto-Trigger（技能自动调用）

**任何 agent 开始任务前，必须先扫描下方索引。用户请求与某技能的触发条件匹配时，立即读取对应 `SKILL.md` 全文并严格遵循，无需用户点名。** 技能可能位于分类目录；按技能名查找唯一的 `content/skills/**/<name>/SKILL.md`，不得假设都在顶层。可多技能叠加。

### 通用技能索引

| 技能 | 触发条件（description 原文） |
| --- | --- |
""" + "\n".join(rows) + """

### 飞书 / Lark 工具（lark-*）

涉及飞书的消息、文档、表格、日历、审批、邮件、会议、OKR 等操作时，读取对应 `content/skills/lark-<域>/SKILL.md`。可用域：
""" + ", ".join(sorted(lark)) + """。

### 维护规则

新增、导入或移动技能后：1) `python3 bin/rebuild-claude-md.py`；2) `bash bin/validate-skills.sh`；3) git 提交。
"""

target = os.path.join(ROOT, "content", "skills", "CLAUDE.md")
if arguments == ["--check"]:
    try:
        with open(target, encoding="utf-8") as handle:
            current = handle.read()
    except OSError as exc:
        print(f"drift: CLAUDE.md 无法读取：{exc}", file=sys.stderr)
        raise SystemExit(1)
    if current != out:
        print("drift: CLAUDE.md 与活动 skills/**/SKILL.md 索引不一致", file=sys.stderr)
        raise SystemExit(1)
    print(f"check ok: CLAUDE.md（通用 {len(rows)} 个，lark {len(lark)} 个域）")
else:
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(out)
    print(f"CLAUDE.md 重建完成：通用 {len(rows)} 个，lark {len(lark)} 个域")
for warning in warnings:
    print("WARN:", warning, file=sys.stderr)
