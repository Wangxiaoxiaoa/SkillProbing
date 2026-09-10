"""SkillProbing 工作流编排: 对每个待测 task 运行"有/无 skill"两组并判断是否内置。

每个 task:
  1. 准备两个工作目录(有/无skill), 拷入输入
  2. 有skill注入提示, 无skill不注入; 各驱动 opencode 经网关执行
  3. verifier 判题(正确性)
  4. 从网关轨迹提取 logprobs -> 答案段 熵/PPL
  5. judge 对比 -> 判定内置
关键token(critical_delta)在多轮不对齐轨迹下可不提供(由 PPL+正确率+熵 主导)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from skillprobing.config import Settings, load_settings
from skillprobing.analysis.probe import judge
from skillprobing.logits.entropy import summarize
from skillprobing.logits.extractor import extract_token_logprobs
from skillprobing.run.agent import build_prompt, prepare_workspace, run_agent
from skillprobing.skills.skill_loader import TaskSpec, load_local_tasks
from skillprobing.tasks.verifier import run_verifier


@dataclass
class Compare:
    settings: Settings = field(default_factory=load_settings)
    tasks: list[TaskSpec] = field(default_factory=list)

    def collect(self, task_ids=None, custom_root: Path | None = None):
        if custom_root is not None:
            from skillprobing.skills.custom_loader import import_user_tasks
            self.tasks = import_user_tasks(custom_root)
        else:
            self.tasks = load_local_tasks(self.settings.skills)
        if task_ids:
            self.tasks = [t for t in self.tasks if t.task_id in task_ids]
        return self.tasks

    def _summarize_trace(self, trace_path: Path) -> dict:
        """从网关 trace 提取所有 token logprobs 并汇总熵/PPL。"""
        all_toks = []
        if trace_path.exists():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                resp = rec.get("response")
                if isinstance(resp, dict):
                    toks = extract_token_logprobs(resp)
                    if toks:
                        all_toks.extend(toks)
        s = summarize(all_toks) if all_toks else None
        return {"n_calls": s.get("n_tokens") if s else 0, "ppl": s.get("ppl") if s else None,
                "entropy": s.get("entropy") if s else None}

    def _run_condition(self, task, out, with_skill, gateway_url, trace_name) -> dict:
        label = "with_skill" if with_skill else "without_skill"
        ws = out / label / "workspace"
        prepare_workspace(ws, task.input_dir if (task.input_dir and task.dir / "input").exists() else None)
        prompt = build_prompt(task.prompt, task.skills_dir if with_skill else None)
        run_agent(self.settings, prompt, ws, gateway_url=gateway_url)
        verdict = run_verifier(task.verifier_dir, ws)
        tr = self._summarize_trace(out / trace_name)
        return {
            "n_calls": tr.get("n_calls", 0),
            "correct": verdict.passed,
            "accuracy": verdict.score,
            "answer_ppl": tr.get("ppl"),
            "answer_entropy": tr.get("entropy"),
        }

    def compare_task(self, task: TaskSpec, gateway_url: str | None = None) -> dict:
        out = self.settings.outputs_dir / task.task_id
        out.mkdir(parents=True, exist_ok=True)
        w = self._run_condition(task, out, True, gateway_url, "with_skill/trace.jsonl")
        wo = self._run_condition(task, out, False, gateway_url, "without_skill/trace.jsonl")
        res = judge(task.task_id, w, wo, self.settings.probe)
        payload = {
            "task_id": task.task_id,
            "verdict": res.verdict.value,
            "reason": res.reason,
            "accuracy_delta": res.accuracy_delta,
            "ppl_delta": res.ppl_delta,
            "critical_kl": res.critical_kl,
            "with_skill": w,
            "without_skill": wo,
        }
        (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def run(self, task_ids=None, gateway_url=None, custom_root=None) -> list[dict]:
        self.collect(task_ids, custom_root=custom_root)
        out = self.settings.outputs_dir
        out.mkdir(parents=True, exist_ok=True)
        results = [self.compare_task(t, gateway_url) for t in self.tasks]
        (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        return results


def main(task_ids=None, gateway_url=None, custom_root=None):
    return Compare(load_settings()).run(task_ids, gateway_url=gateway_url, custom_root=custom_root)