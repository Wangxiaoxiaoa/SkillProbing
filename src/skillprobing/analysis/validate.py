"""对照验证表: 对每个 skill/task,独立记录
  - 熵判断结果 (builtin / not_builtin / inconclusive)
  - verifier 金标准结果 (pass / fail / error)
两者不做交互/一致性计算,只并列表格输出。

用法:
  1. 先用 gateway+agent 跑出两条轨迹:
       with_skill/trace.jsonl, without_skill/trace.jsonl
  2. 调用本模块: 读取轨迹算熵判断, 同时运行 task 的 verifier 得金标准。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from skillprobing.analysis.probe import judge
from skillprobing.logits.entropy import summarize
from skillprobing.logits.extractor import extract_token_logprobs
from skillprobing.tasks.verifier import Verdict, run_verifier


@dataclass
class SkillEvaluation:
    skill_name: str
    task_id: str
    entropy_verdict: str | None = None      # builtin / not_builtin / inconclusive
    entropy_reason: str = ""
    verifier_verdict: str | None = None     # pass / fail / error
    verifier_detail: str = ""
    meta: dict = field(default_factory=dict)


def _read_trace(trace_path: Path) -> list:
    toks = []
    if not trace_path.exists():
        return toks
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
            toks.extend(extract_token_logprobs(resp))
    return toks


def entropy_judge(with_trace: Path, without_trace: Path, cfg, task_id: str = "") -> tuple[str, str]:
    """根据两条轨迹(有/无skill)计算熵判定。"""
    w_toks = _read_trace(with_trace)
    wo_toks = _read_trace(without_trace)
    w_sum = summarize(w_toks)
    wo_sum = summarize(wo_toks)
    with_g = {"correct": False, "accuracy": 0.0, "n_calls": len(w_toks),
              "answer_ppl": w_sum.get("ppl"), "answer_entropy": w_sum.get("entropy")}
    without_g = {"correct": False, "accuracy": 0.0, "n_calls": len(wo_toks),
                 "answer_ppl": wo_sum.get("ppl"), "answer_entropy": wo_sum.get("entropy")}
    res = judge(task_id, with_g, without_g, cfg)
    return res.verdict.value, res.reason


def verifier_judge(task_dir: Path) -> tuple[str, str]:
    """运行 task 的 verifier,返回 (verdict_str, detail)。"""
    from skillprobing.run.agent import prepare_workspace
    from skillprobing.skills.skill_loader import load_task_dir
    task = load_task_dir(task_dir)
    ws = task_dir / ".verify_workspace"
    prepare_workspace(ws, task.input_dir)
    # 若 task_dir 下已有智能体输出(output.json/mass_report.json 等), verifier 会检查它们
    v: Verdict = run_verifier(task_dir, ws)
    verdict = "pass" if v.passed else "fail"
    return verdict, v.detail[:400]


def evaluate(task_dir: Path, with_trace: Path | None, without_trace: Path | None,
             cfg, task_id: str = "") -> SkillEvaluation:
    """对一个 task: 分别做熵判断(需要两条轨迹)和金标准判断(需要 task 目录)。"""
    task = __import__("skillprobing.skills.skill_loader", fromlist=["load_task_dir"]).load_task_dir(task_dir)
    ev = SkillEvaluation(skill_name=task.skill_id or task.task_id, task_id=task.task_id)

    # verifier 金标准
    v, detail = verifier_judge(task_dir)
    ev.verifier_verdict = v
    ev.verifier_detail = detail

    # 熵判断(轨迹可选)
    if with_trace and without_trace and with_trace.exists() and without_trace.exists():
        ev.entropy_verdict, ev.entropy_reason = entropy_judge(with_trace, without_trace, cfg, task_id)
    else:
        ev.entropy_verdict = "pending"
        ev.entropy_reason = "缺少 with/without 轨迹"
    return ev


def build_table(evaluations: list[SkillEvaluation]) -> list[dict]:
    return [
        {
            "skill_name": e.skill_name,
            "task_id": e.task_id,
            "entropy_verdict": e.entropy_verdict,
            "verifier_verdict": e.verifier_verdict,
            "entropy_reason": e.entropy_reason,
        }
        for e in evaluations
    ]


def save_table(table: list[dict], out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = out.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["skill_name", "task_id",
                                                "entropy_verdict", "verifier_verdict"])
        writer.writeheader()
        for row in table:
            writer.writerow({k: row[k] for k in writer.fieldnames})
