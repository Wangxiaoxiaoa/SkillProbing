"""用户自定义 skill/任务/判据 导入。

允许用户提供自己的 skill + 任务描述 + 判题标准(verifier),
格式与 skillsbench 一致:

  my_task/
    task.md                    # frontmatter + 正文, 正文是给智能体的指令
    skills/<name>/SKILL.md     # 可选: 该任务配套的 skill
    verifier/                  # 判题标准:
        # 两种之一:
        test_outputs.py        # pytest 断言(检查输出文件/内容)
        (或 test.sh           # 返回 reward)
    输入文件                 # 可选, 拷到工作区供智能体用

本项目用统一的 TaskSpec 结构跑"有/无 skill"的对比判断。
"""
from __future__ import annotations

from pathlib import Path

from skillprobing.skills.skill_loader import TaskSpec, _task_text


def import_user_task(task_dir: Path) -> TaskSpec:
    """把一个用户自定义任务目录解析为 TaskSpec(就地使用, 不复制)。

    期望结构见模块 docstring。缺失 skill/verifier 会降级。
    """
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"自定义任务目录不存在: {task_dir}")

    skills_dir = task_dir / "skills"
    verifier_dir = task_dir / "verifier"
    input_dir = task_dir / "input"

    skill_names = [p.name for p in sorted(skills_dir.iterdir()) if p.is_dir()] \
        if skills_dir.is_dir() else []

    oracle = task_dir / "oracle" if (task_dir / "oracle").is_dir() else None

    return TaskSpec(
        task_id=task_dir.name,
        dir=task_dir,
        prompt=_task_text(task_dir),
        skills_dir=skills_dir if skills_dir.is_dir() else Path(""),
        verifier_dir=verifier_dir if verifier_dir.is_dir() else Path(""),
        input_dir=input_dir if input_dir.is_dir() else Path(""),
        skill_names=skill_names,
        oracle_path=oracle,
    )


def import_user_tasks(root: Path) -> list[TaskSpec]:
    """从 root 下每个子目录导入。"""
    return [import_user_task(d) for d in sorted(root.iterdir()) if d.is_dir()]