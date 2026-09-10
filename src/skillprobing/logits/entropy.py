"""轨迹 token 级指标计算。

数据来源: 网关截获的 trace, 已知每个生成 token 的 topK logprobs。
指标:
  - entropy                    每个/整段 token 的信息熵
  - kl(有,无)                  同位置两分布 KL 散度(关键token定位用)
  - per_token                    整段平均负对数似然 = log PPL
关键 token 定位: 在有/无skill的可对齐前缀上逐位置扫描 Δ(label), 找峰值。
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from skillprobing.logits.extractor import TokenLogprob


def _softmax(logprobs: list[float]) -> list[float]:
    if not logprobs:
        return []
    m = max(logprobs)
    exps = [math.exp(lp - m) for lp in logprobs]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


def token_entropy(top: Sequence[tuple[Any, float]] | None, fallback: float | None) -> float:
    """单 token 熵: 用 topK 分布(归一化)或 fallback 单值。"""
    vals = [lp for _, lp in top] if top else ([fallback] if fallback is not None else [])
    if not vals:
        return 0.0
    probs = _softmax(vals)
    return -sum(p * math.log(p) for p in probs if p > 0)


def sequence_entropy(tokens: Sequence[TokenLogprob]) -> float:
    if not tokens:
        return 0.0
    return sum(token_entropy(t.top, t.logprob) for t in tokens) / len(tokens)


def sequence_nll(tokens: Sequence[TokenLogprob]) -> float:
    """整段的平均负对数似然(-log p 每个被采token)。PPL = exp(该值)。"""
    if not tokens:
        return 0.0
    return sum(-t.logprob for t in tokens) / len(tokens)


def sequence_ppl(tokens: Sequence[TokenLogprob]) -> float:
    nll = sequence_nll(tokens)
    return math.exp(min(nll, 709))  # 防溢出


def kl_div(a: Sequence[float], b: Sequence[float]) -> float:
    """两个 logprob 分布 a,b (均已给定各 top logprob) 的 KL(a||b)。"""
    pa, pb = _softmax(a), _softmax(b)
    return sum((pa[i] * math.log(pa[i] / pb[i]) for i in range(min(len(pa), len(pb)))
                if pa[i] > 0 and pb[i] > 0), 0.0)


def critical_positions(with_toks: Sequence[TokenLogprob],
                       without_toks: Sequence[TokenLogprob],
                       topn: int = 0) -> dict:
    """在对齐长度上逐位置算 |Δ{entropy}|, 返回峰值(关键token)信息。

    注意: 仅当两端长度一致(单次受控生成)才有意义;否则返回空。
    """
    n = min(len(with_toks), len(without_toks))
    if n == 0:
        return {"pos": -1, "delta": 0.0, "alignable": False}
    deltas = []
    for i in range(n):
        wt, wo = with_toks[i], without_toks[i]
        if topn > 0:
            wtop = [lp for _, lp in wt.top[:topn]] if wt.top else [wt.logprob]
            otop = [lp for _, lp in wo.top[:topn]] if wo.top else [wo.logprob]
            d = abs(token_entropy(wt.top, wt.logprob) - token_entropy(wo.top, wo.logprob))
        else:
            d = abs(wt.logprob if wt.logprob is not None else 0.0 - (wo.logprob or 0.0))
        deltas.append(d)
    pos = int(max(range(n), key=deltas.__getitem__))
    return {"pos": pos, "delta": round(deltas[pos], 4), "n": n, "alignable": True}


def summarize(tokens: Sequence[TokenLogprob]) -> dict:
    if not tokens:
        return {"n_tokens": 0, "entropy": 0.0, "ppl": 0.0, "mean_logprob": 0.0}
    return {
        "n_tokens": len(tokens),
        "entropy": round(sequence_entropy(tokens), 4),
        "ppl": round(sequence_ppl(tokens), 4),
        "mean_logprob": round(sequence_nll(tokens), 4),
    }