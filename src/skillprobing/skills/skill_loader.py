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
    skill_id: str | None = None   # 主要 skill 名称(用于表格展示)
    oracle_path: Path | None = None


def load_task_dir(task_dir: Path) -> TaskSpec:
    """从单个已抽取任务目录加载 TaskSpec。"""
    # 优先使用保留的 environment/skills/; 不存在则回退到旧 skills/
    skills_dir = task_dir / "environment" / "skills" if (task_dir / "environment" / "skills").is_dir() else task_dir / "skills"
    skill_names = [p.name for p in sorted(skills_dir.iterdir()) if p.is_dir()] if skills_dir.is_dir() else []
    return TaskSpec(
        task_id=task_dir.name,
        dir=task_dir,
        prompt=_task_text(task_dir),
        skills_dir=skills_dir,
        verifier_dir=task_dir / "verifier",
        input_dir=task_dir / "input",
        skill_names=skill_names,
        skill_id=skill_names[0] if skill_names else task_dir.name,
        oracle_path=task_dir / "oracle" if (task_dir / "oracle").is_dir() else None,
    )


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

        # 1) 保留完整 environment/ 目录(含 Dockerfile、skills、输入文件)
        src_env = task_dir / "environment"
        dst_env = dest_task / "environment"
        if src_env.is_dir():
            shutil.copytree(src_env, dst_env, dirs_exist_ok=True)
        # 向后兼容: 同时保留 input/ 作为输入文件副本
        dst_input = dest_task / "input"
        if src_env.is_dir():
            for f in src_env.iterdir():
                if f.name in ("skills", "Dockerfile") or f.is_dir():
                    continue
                dst_input.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dst_input / f.name)
        # 同时保留顶层 skills/ 副本,兼容 load_task_dir 旧路径
        if (dst_env / "skills").is_dir():
            shutil.copytree(dst_env / "skills", dest_task / "skills", dirs_exist_ok=True)

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

        skill_names: list[str] = []
        skills_dir = dst_env / "skills" if (dst_env / "skills").is_dir() else dest_task / "skills"
        if skills_dir.is_dir():
            skill_names = [p.name for p in sorted(skills_dir.iterdir()) if p.is_dir()]

        out.append(TaskSpec(
            task_id=task_id,
            dir=dest_task,
            prompt=_task_text(dest_task),
            skills_dir=skills_dir,
            verifier_dir=dest_task / "verifier",
            input_dir=dest_task / "input",
            skill_names=skill_names,
            skill_id=skill_names[0] if skill_names else task_id,
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
        out.append(load_task_dir(task_dir))
    return out


def _task_text(task_dir: Path) -> str:
    md = task_dir / "task.md"
    if not md.exists():
        return ""
    text = md.read_text(encoding="utf-8", errors="ignore")
    parts = text.split("---")
    return "---".join(parts[2:]) if len(parts) >= 3 else text
