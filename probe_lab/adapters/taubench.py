"""tau-bench 适配器: 100 个客服任务(airline 50 + retail 50)。

- 物料: task_text=instruction; gold_trace=actions(标准工具调用序列, 官方标注);
        identifiers=工具名。skill 注入: 环境自带 wiki.md(业务规则, 原生 skill)。
- 金标准: 需要 LLM 用户模拟器多轮对话 + 环境状态 reward, 官方实现依赖
  openai 客户端兼容接口。骨架在此, 完整闭环二期(用户模拟器成本高)。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from base import BenchmarkAdapter, ProbeMaterial, Verdict

TAU_ROOT = Path("/data1/xiao/tau-bench")
ENVS = ["airline", "retail"]


class TauBenchAdapter(BenchmarkAdapter):
    name = "taubench"
    supports_gold = False   # 金标准需用户模拟器, 二期

    def __init__(self):
        self._cache: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = {}
            for env in ENVS:
                tasks_file = TAU_ROOT / f"tau_bench/envs/{env}/tasks.py"
                text = tasks_file.read_text(errors="ignore")
                # tasks.py 是 python 字面量列表, 用 ast 提取
                m = re.search(r"^tasks\s*=\s*(\[.*?\])\s*$", text, re.S | re.M)
                if not m:
                    # 文件可能是多个 dict 拼接, 退化为逐块解析
                    continue
                try:
                    tasks = ast.literal_eval(m.group(1))
                except Exception:
                    continue
                for i, t in enumerate(tasks):
                    self._cache[f"{env}_{i}"] = {**t, "_env": env}
        return self._cache

    def list_tasks(self) -> list[str]:
        return sorted(self._load().keys())

    def load_material(self, task_id: str) -> ProbeMaterial:
        x = self._load()[task_id]
        env = x["_env"]
        # gold trace: actions 序列 -> 文本化
        calls = []
        for a in x.get("actions", []):
            calls.append(f"{a['name']}(**{json.dumps(a.get('arguments', {}), ensure_ascii=False)})")
        gold = "\n".join(calls)
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(
            task_id=f"taubench_{task_id}",
            skill_name=f"{env}-domain",
            skill_md=self.load_skill_md(task_id),
            task_text=x.get("instruction", "")[:1500],
            gold_trace=gold,
            identifiers=idents,
            key_spans=spans,
        )

    def load_skill_md(self, task_id: str) -> str:
        """skill = 该环境的业务规则文档 wiki.md(原生 skill, 与 skillsbench SKILL.md 等价)。"""
        x = self._load()[task_id]
        env = x["_env"]
        wiki = TAU_ROOT / f"tau_bench/envs/{env}/wiki.md"
        if wiki.exists():
            return wiki.read_text(errors="ignore")[:4000]
        return ""

    # 金标准二期: 需要 LLM 用户模拟器 + env 状态 reward
    # 参考: tau_bench/envs/{env}/env.py 的 Env.run_rp traj 判定逻辑
