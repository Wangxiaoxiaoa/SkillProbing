"""terminal-bench 适配器: 241 个终端任务(容器沙箱 + pytest 判分)。

- 物料: task_text=task.yaml instruction; gold_trace=solution.sh(官方解法)。
- skill 注入: solution.sh 提炼的操作要点(直接给出官方解法作为技能知识,
  与 skillsbench 的 SKILL.md 地位等同)。
- 金标准: docker build(任务 Dockerfile) + agent(opencode) 在容器内执行
  + 容器内 run-tests.sh/pytest 判分。与 skillsbench 管线同构。
  注: agent 在容器内跑需要挂载 opencode, 采用 skillsbench docker_runner 同款挂载方案。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import yaml

from base import BenchmarkAdapter, ProbeMaterial, Verdict

TB_ROOT = Path("/data1/xiao/terminal-bench")


class TerminalBenchAdapter(BenchmarkAdapter):
    name = "terminalbench"
    supports_gold = True

    def _task_dir(self, task_id: str) -> Path:
        return TB_ROOT / "original-tasks" / task_id

    def list_tasks(self) -> list[str]:
        return sorted(p.name for p in (TB_ROOT / "original-tasks").iterdir() if p.is_dir())

    def load_material(self, task_id: str) -> ProbeMaterial:
        td = self._task_dir(task_id)
        meta = yaml.safe_load((td / "task.yaml").read_text(errors="ignore"))
        instruction = meta.get("instruction", "")
        # gold trace: solution.sh
        gold = ""
        sol = td / "solution.sh"
        if sol.exists():
            gold = sol.read_text(errors="ignore")[:2000]
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(
            task_id=f"tb_{task_id}",
            skill_name=task_id,
            skill_md=self.load_skill_md(task_id),
            task_text=instruction[:2000],
            gold_trace=gold,
            identifiers=idents,
            key_spans=spans,
        )

    def load_skill_md(self, task_id: str) -> str:
        td = self._task_dir(task_id)
        sol = td / "solution.sh"
        if not sol.exists():
            return ""
        return ("# 完成该类终端任务的操作要点(官方参考操作)\n"
                "```bash\n" + sol.read_text(errors="ignore")[:1500] + "\n```")

    # ---- 金标准: 容器内跑 agent + 判分(重, 单任务 5-20 分钟) ----
    def run_gold(self, task_id: str, with_skill: bool,
                 agent_run=None, workspace: Path | None = None) -> Verdict:
        """agent_run(prompt, ws) -> None 由 runner 提供(容器内 agent 执行)。

        简化版: 先只实现 verifier 部分(agent 产物由外部写入 workspace)。
        完整闭环(容器内 opencode)作为二期, 与 skillsbench docker_runner 对齐。
        """
        td = self._task_dir(task_id)
        img = f"sb-recheck/tb-{task_id}"
        rc, log = subprocess.run(
            ["sudo", "docker", "build", "--network", "host",
             "--build-arg", "HTTP_PROXY=http://127.0.0.1:18080",
             "--build-arg", "HTTPS_PROXY=http://127.0.0.1:18080",
             "-t", img, "."],
            capture_output=True, text=True, timeout=2400,
            cwd=str(td)).returncode, ""
        if rc != 0:
            return Verdict(False, 0.0, f"build 失败")
        ws = workspace or (OUT_DEFAULT := Path("/data1/xiao/SkillProbing/outputs/gold_standard/tb") / task_id / ("with_skill" if with_skill else "without_skill") / "workspace")
        ws.mkdir(parents=True, exist_ok=True)
        # 容器内判分: run-tests.sh 或 tests/test_outputs.py
        cmd = ["sudo", "docker", "run", "--rm", "--network", "host",
               "-v", f"{ws.resolve()}:/app/agent_output",
               img, "bash", "-c",
               "pip install pytest --quiet 2>/dev/null; "
               "cd /app 2>/dev/null || cd /root; "
               "python3 -m pytest /mnt/tests/test_outputs.py -q 2>&1 | tail -4"]
        tests_dir = td / "tests"
        cmd.insert(cmd.index("-v") + 1, f"{tests_dir.resolve()}:/mnt/tests:ro")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        combined = r.stdout + r.stderr
        passed = "failed" not in combined.lower() and ("passed" in combined.lower() or r.returncode == 0)
        return Verdict(passed, 1.0 if passed else 0.0, combined[-250:])

    def run_gold_batchable(self) -> bool:
        return False   # 需要 agent 闭环, 成本高
