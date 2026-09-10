"""驱动智能体(opencode)运行任务, 支持 无skill / 有skill注入。

输出: 智能体写入工作目录的结果文件 + 网关截获的 LLM 轨迹。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skillprobing.config import AgentConfig, Settings


@dataclass
class AgentRun:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool


def build_prompt(task_prompt: str, skills_dir: Path | None) -> str:
    """构造给智能体的提示。skills_dir 存在时注入对应 skill 内容。"""
    if skills_dir is None or not skills_dir.is_dir():
        return task_prompt
    extra = []
    for s in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skmd = s / "SKILL.md"
        if skmd.exists():
            extra.append(f"--- skill: {s.name} ---\n{skmd.read_text(encoding='utf-8', errors='ignore')}")
    if extra:
        return task_prompt + "\n\n[可用技能]\n" + "\n\n".join(extra)
    return task_prompt


def prepare_workspace(ws: Path, task_input_dir: Path | None) -> Path:
    """创建独立工作目录并拷入任务输入文件。"""
    ws.mkdir(parents=True, exist_ok=True)
    if task_input_dir is not None and task_input_dir.is_dir():
        for f in task_input_dir.iterdir():
            if f.is_file():
                shutil.copy(f, ws / f.name)
    return ws


def run_agent(cfg: Settings, prompt: str, ws: Path,
              gateway_url: str | None = None, timeout: int = 600) -> AgentRun:
    """调用 opencode 执行任务。gateway_url 非空时走网关(截获输入输出+logprobs)。"""
    acfg = cfg.agent
    cmd = [acfg.opencode_bin, "run"]
    if gateway_url and not acfg.model:
        # 网关场景需要配置模型指向网关, 这里通过 --model 传
        pass
    if getattr(acfg, "model", None):
        cmd += ["--model", acfg.model]
    cmd += [prompt]
    env = dict(os.environ)
    if gateway_url:
        env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    try:
        r = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return AgentRun(r.returncode, r.stdout, r.stderr, False)
    except subprocess.TimeoutExpired:
        return AgentRun(None, "", "", True)