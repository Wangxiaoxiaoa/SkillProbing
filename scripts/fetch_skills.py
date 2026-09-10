#!/usr/bin/env python3
"""从 skillsbench 抽取指定的 skills/tasks 到本项目 skills_tasks 目录.

用法:
  python scripts/fetch_skills.py                   # 抽取全部 tasks(87)
  python scripts/fetch_skills.py 3d-scan-calc     # 只抽指定 task
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from skillprobing.config import SkillsConfig  # noqa: E402
from skillprobing.skills.skill_loader import extract_tasks  # noqa: E402


def main():
    task_ids = sys.argv[1:] or None
    cfg = SkillsConfig()
    specs = extract_tasks(cfg, task_ids)
    print(f"抽取 {len(specs)} 个 task 到: {cfg.skills_dir}")
    for s in specs:
        print(f"  - {s.task_id}  skills={s.skill_names}")


if __name__ == "__main__":
    main()