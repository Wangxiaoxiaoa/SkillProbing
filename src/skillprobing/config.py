"""SkillProbing 配置。

集中定义:
- 推理后端地址(用于获取 logprobs/logits)
- skillsbench 的来源目录(仅用于一次性抽取物料, 运行不依赖)
- 本项目自有 skills 目录(运行读取)
- 智能体(opencode)配置
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # SkillProbing/


@dataclass
class BackendConfig:
    """用于获取 token logprobs 的推理后端(需支持 return_logprob)。"""
    base_url: str = os.environ.get("BACKEND_URL", "http://127.0.0.1:30001/v1")
    api_key: str = os.environ.get("BACKEND_KEY", "")
    model: str = os.environ.get("BACKEND_MODEL", "/mnt/model")
    max_new_tokens: int = 512
    temperature: float = 0.0


@dataclass
class SkillsConfig:
    """物料来源与本地抽取目录。运行期只读本地 skills 目录。"""
    # 来源: skillsbench 的 tasks 根目录(仅抽取用)
    bench_tasks_root: Path = Path("/mnt/data/xiao/RL/skillsbench/tasks")
    # 本项目自有的 skills 目录(抽取后存放, 运行读这里)
    skills_dir: Path = PROJECT_ROOT / "skills_tasks"


@dataclass
class AgentConfig:
    """智能体驱动配置(opencode)。"""
    opencode_bin: str = "opencode"
    model: str = "dsv4/dsv4-flash"
    # skill 注入到 smart agent 的方式: prompt(system/AGENTS) 或 tool
    skill_inject: str = "prompt"


@dataclass
class ProbeConfig:
    """对比分析参数。"""
    top_logprobs: int = 5          # 每个位置取 topK logprobs
    entropy_metric: str = "entropy"  # entropy / argmax_margin
    threshold_delta: float = 0.05   # 有/无skill 结果正确率差阈值
    threshold_entropy: float = 0.2  # 有/无skill 熵差阈值


@dataclass
class Settings:
    backend: BackendConfig = field(default_factory=BackendConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    probe: ProbeConfig = field(default_factory=ProbeConfig)

    outputs_dir: Path = PROJECT_ROOT / "outputs"


def load_settings() -> Settings:
    return Settings()