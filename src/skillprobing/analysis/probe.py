"""SkillProbe 判定: 有/无 skill 两组对比 -> skill 是否被模型内置。

依赖三个指标(同一"答案"层):
  1. correctness : verifier 结果对错
  2. critical    : 关键 token 位置上有/无 skill 的分布差异(Δ熵 / KL)
  3. answer_ppl  : 答案段 PPL(模型是否顺/确定)

判据:
  BUILTIN      : 无skill也答对 且 关键位差异≈0 且有/无PPL接近
  NOT_BUILTIN  : 有skill才答对 或 Δacc明显 / 关键位差异明显
  INCONCLUSIVE : 证据不足
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    BUILTIN = "builtin"
    NOT_BUILTIN = "not_builtin"
    INCONCLUSIVE = "inconclusive"


@dataclass
class GroupStats:
    task_id: str
    with_skill: bool
    correct: bool
    accuracy: float
    n_calls: int = 0
    answer_ppl: float | None = None
    answer_entropy: float | None = None
    critical_pos: int | None = None
    critical_delta: float | None = None


@dataclass
class ProbeResult:
    task_id: str
    verdict: Verdict
    with_group: GroupStats
    without_group: GroupStats
    reason: str
    accuracy_delta: float | None = None
    ppl_delta: float | None = None
    critical_kl: float | None = None
    detail: dict = field(default_factory=dict)


def _f(v):
    return None if v is None else float(v)


def _mk(task_id: str, with_skill: bool, raw: dict) -> GroupStats:
    return GroupStats(
        task_id=task_id,
        with_skill=with_skill,
        correct=bool(raw.get("correct", False)),
        accuracy=float(raw.get("accuracy", 0.0)),
        n_calls=int(raw.get("n_calls", 0)),
        answer_ppl=_f(raw.get("answer_ppl")),
        answer_entropy=_f(raw.get("answer_entropy")),
        critical_pos=raw.get("critical_pos"),
        critical_delta=_f(raw.get("critical_delta")),
    )


def _th(cfg, name, default):
    return getattr(cfg, name, default)


def judge(task_id: str, with_g: dict, without_g: dict, cfg) -> ProbeResult:
    wg = _mk(task_id, True, with_g)
    wog = _mk(task_id, False, without_g)

    acc_delta = wg.accuracy - wog.accuracy
    ppl_delta = None if (wg.answer_ppl is None or wog.answer_ppl is None) \
        else wg.answer_ppl - wog.answer_ppl
    crit = None
    if wg.critical_delta is not None and wog.critical_delta is not None:
        crit = round(abs(wg.critical_delta - wog.critical_delta), 4)
    elif wog.critical_delta is not None:
        crit = round(abs(wog.critical_delta), 4)

    t_acc = _th(cfg, "threshold_delta", 0.05)
    t_crit = _th(cfg, "threshold_entropy", 0.2)
    t_ppl = 0.2

    reasons = []
    no_skill_ok = wog.accuracy >= 0.5
    acc_close = abs(acc_delta) <= t_acc
    crit_close = (crit is None) or (crit <= t_crit)
    ppl_close = (ppl_delta is None) or (abs(ppl_delta) <= t_ppl + 1e-9)

    if no_skill_ok:
        reasons.append("无技能也答对")
    if acc_close:
        reasons.append(f"Δacc≈0({acc_delta:.3f})")
    if crit_close:
        reasons.append(f"关键位Δ≈0({crit})")
    if ppl_close:
        reasons.append(f"PPL接近(Δ={ppl_delta})")

    # 内置: 无skill本身答对 + 关键位分布无差异 + PPL接近(模型很顺)
    if no_skill_ok and crit_close and ppl_close:
        v = Verdict.BUILTIN
    elif (not no_skill_ok and wg.accuracy >= 0.5) or acc_delta > 0.15:
        reasons.append("只有有skill才答对/明显更准")
        v = Verdict.NOT_BUILTIN
    else:
        reasons.append("证据不足")
        v = Verdict.INCONCLUSIVE

    return ProbeResult(
        task_id=task_id, verdict=v, with_group=wg, without_group=wog,
        accuracy_delta=round(acc_delta, 4),
        ppl_delta=round(ppl_delta, 4) if ppl_delta is not None else None,
        critical_kl=crit, reason="; ".join(reasons),
    )