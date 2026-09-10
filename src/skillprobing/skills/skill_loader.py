"""从 skillsbench tasks 抽取"待测 skill + 任务 + 判题标准"到本项目。

仅一次性抽取物料(运行期不依赖 skillsbench 仓库)。本项目只读取
本地的 skills_tasks/ 目录运行,独立重构判断逻辑。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from skillprobing.config import SkillsConfig


@dataclass
class TaskSpec:
    """一个任务在本项目中的位置与内容。"""
    task_id: str
    dir: Path
    prompt: str                # 用户任务(task.md 正文)
    skills_dir: Path           # 该任务的 skills/
    verifier_dir: Path         # 判题脚本(独立)
    input_dir: Path            # 输入文件
    skill_names: list[str]
    oracle_path: Path | None


def extract_tasks(cfg: SkillsConfig, task_ids: list[str] | None = None) -> list[TaskSpec]:
    """从 skillsbench 抽取指定(或全部)任务到本项目的 skills_tasks 目录。

    独立写入 ol. extract 后运行期不看 skillsbench。
    """
    tasks_root = cfg.bench_tasks_root
    dest_root = cfg.skills_dir
    dest_root.mkdir(parents=True, exist_ok=True)
    out: list[TaskSpec] = []

    cand = sorted(tasks_root.glob("*/")) if task_ids is None else [tasks_root / t for t in task_ids]
    for task_dir in cand:
        if not task_dir.is_dir():
            print(f"跳过(不存在): {task_dir}")
            continue
        task_id = task_dir.name
        dest_task = dest_root / task_id
        dest_task.mkdir(parents=True, exist_ok=True)

        # 1) skills
        src_skill = task_dir / "environment" / "skills"
        skill_names: list[str] = []
        if src_skill.is_dir():
            shutil.copytree(src_skill, dest_task / "skills", dirs_exist_ok=True)
            skill_names = [p.name for p in sorted(src_skill.iterdir()) if p.is_dir()]

        # 2) task.md
        if (task_dir / "task.md").exists():
            shutil.copy(task_dir / "task.md", dest_task / "task.md")

        # 3) verifier
        if (task_dir / "verifier").is_dir():
            shutil.copytree(task_dir / "verifier", dest_task / "verifier", dirs_exist_ok=True)

        # 4) oracle
        oracle_path: Path | None = None
        if (task_dir / "oracle").is_dir():
            shutil.copytree(task_dir / "oracle", dest_task / "oracle", dirs_exist_ok=True)
            oracle_path = dest_task / "oracle"

        # 5) 输入文件(env 非 skills/Dockerfile 的文件)
        src_env = task_dir / "environment"
        dst_input = dest_task / "input"
        if src_env.is_dir():
            for f in src_env.iterdir():
                if f.name in ("skills", "Dockerfile") or f.is_dir():
                    continue
                dst_input.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dst_input / f.name)

        out.append(TaskSpec(
            task_id=task_id,
            dir=dest_task,
            prompt=_task_text(dest_task),
            skills_dir=dest_task / "skills",
            verifier_dir=dest_task / "verifier",
            input_dir=dest_task / "input",
            skill_names=skill_names,
            oracle_path=oracle_path,
        ))
    return out


def load_local_tasks(cfg: SkillsConfig) -> list[TaskSpec]:
    """运行期从本项目 skills_tasks 目录加载已抽取的任务(不依赖 skillsbench)。"""
    out: list[TaskSpec] = []
    if not cfg.skills_dir.is_dir():
        return out
    for task_dir in sorted(cfg.skills_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        skill_names = [p.name for p in sorted((task_dir / "skills").iterdir()) if p.is_dir()] \
            if (task_dir / "skills").is_dir() else []
        out.append(TaskSpec(
            task_id=task_dir.name,
            dir=task_dir,
            prompt=_task_text(task_dir),
            skills_dir=task_dir / "skills",
            verifier_dir=task_dir / "verifier",
            input_dir=task_dir / "input",
            skill_names=skill_names,
            oracle_path=task_dir / "oracle" if (task_dir / "oracle").is_dir() else None,
        ))
    return out


def _task_text(task_dir: Path) -> str:
    md = task_dir / "task.md"
    if not md.exists():
        return ""
    text = md.read_text(encoding="utf-8", errors="ignore")
    parts = text.split("---")
    return "---".join(parts[2:]) if len(parts) >= 3 else text