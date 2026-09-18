#!/usr/bin/env python3
"""共享 Skill 仓库的唯一发现与结构校验入口。

活动范围：
- 顶层 ``skills/<name>/SKILL.md``；
- 带 ``DESCRIPTION.md`` 的顶层分类目录中的任意深度子 Skill。

普通来源包即使含嵌套 ``SKILL.md``，没有分类标记也不进入自动索引/打包。
隐藏目录、断链和重名采用 fail-closed 或显式告警，避免宿主加载歧义。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)
DESCRIPTION_RE = re.compile(
    r"^description:\s*(.+?)(?=\n[A-Za-z_-]+:|\Z)", re.S | re.M
)


class InventoryError(ValueError):
    """活动 Skill 清单不唯一或无法安全建立。"""


@dataclass(frozen=True)
class SkillRecord:
    name: str
    directory: Path
    skill_md: Path
    relative_directory: str
    description: str


def _frontmatter_text(skill_md: Path) -> str | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def frontmatter_description(skill_md: Path) -> str:
    block = _frontmatter_text(skill_md)
    if block is None:
        return ""
    match = DESCRIPTION_RE.search(block)
    if not match:
        return ""
    return (
        " ".join(match.group(1).split())
        .strip('"\' >|')
        .replace("|", "\\|")
    )


def _record(skills_root: Path, skill_md: Path) -> SkillRecord:
    directory = skill_md.parent
    return SkillRecord(
        name=directory.name,
        directory=directory,
        skill_md=skill_md,
        relative_directory=directory.relative_to(skills_root).as_posix(),
        description=frontmatter_description(skill_md),
    )


def _nested_skill_exists(top: Path) -> bool:
    for current, dirs, files in os.walk(top, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if Path(current) == top:
            continue
        if "SKILL.md" in files:
            return True
    return False


def discover_skills(skills_root: Path) -> tuple[list[SkillRecord], list[str]]:
    """返回唯一活动 Skill 清单和非阻断告警。"""
    skills_root = Path(skills_root).resolve()
    records: list[SkillRecord] = []
    warnings: list[str] = []

    for top in sorted(skills_root.iterdir(), key=lambda path: path.name):
        if top.name.startswith("."):
            continue
        if top.is_symlink() and not top.exists():
            warnings.append(f"跳过（断链）: {top.name}")
            continue
        if not top.is_dir():
            continue

        top_skill = top / "SKILL.md"
        if top_skill.is_file():
            records.append(_record(skills_root, top_skill))

        category_marker = top / "DESCRIPTION.md"
        if category_marker.is_file():
            for current, dirs, files in os.walk(top, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                current_path = Path(current)
                if current_path == top:
                    continue
                if "SKILL.md" in files:
                    records.append(_record(skills_root, current_path / "SKILL.md"))
        elif not top_skill.is_file():
            if not _nested_skill_exists(top):
                warnings.append(f"跳过（顶层目录无 SKILL.md）: {top.name}")
            # 有嵌套 Skill、但无 DESCRIPTION.md：视为来源包，不进入活动索引。

    by_name: dict[str, list[SkillRecord]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)
    duplicates = {
        name: sorted(items, key=lambda item: item.relative_directory)
        for name, items in by_name.items()
        if len(items) > 1
    }
    if duplicates:
        detail = "; ".join(
            f"{name} -> {', '.join(item.relative_directory for item in items)}"
            for name, items in sorted(duplicates.items())
        )
        raise InventoryError(f"活动 Skill 名称重复，拒绝猜测：{detail}")

    return sorted(records, key=lambda item: item.name), warnings


def validate_records(records: Iterable[SkillRecord]) -> list[str]:
    problems: list[str] = []
    for record in records:
        try:
            text = record.skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            problems.append(f"无法读取 {record.relative_directory}/SKILL.md: {exc}")
            continue
        block_match = FRONTMATTER_RE.match(text)
        if not block_match:
            problems.append(f"missing frontmatter fence: {record.relative_directory}")
            continue
        block = block_match.group(1)
        if not re.search(r"(?m)^name:\s+\S", block):
            problems.append(f"missing frontmatter name: {record.relative_directory}")
        if not DESCRIPTION_RE.search(block):
            problems.append(f"missing frontmatter description: {record.relative_directory}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发现和校验共享 Skill 活动清单")
    parser.add_argument(
        "action", choices=("list", "validate"), help="列出清单或校验结构"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "content" / "skills",
        help="skills 根目录",
    )
    parser.add_argument("--format", choices=("tsv", "json"), default="tsv")
    args = parser.parse_args(argv)

    try:
        records, warnings = discover_skills(args.root)
    except (OSError, InventoryError) as exc:
        print(f"inventory error: {exc}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)

    if args.action == "validate":
        problems = validate_records(records)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"skills look good: {len(records)} active skills")
        return 0

    if args.format == "json":
        print(
            json.dumps(
                [
                    {
                        "name": record.name,
                        "relative_directory": record.relative_directory,
                        "description": record.description,
                    }
                    for record in records
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for record in records:
            print(f"{record.name}\t{record.relative_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
