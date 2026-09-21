"""五种零环境探测方案 (S1-S5), 支持任意数量任务。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from sglang_client import SGLangConfig, completion_logprobs, prompt_logprobs, tokenize


@dataclass
class SchemeResult:
    scheme: str
    task_id: str
    score: float
    verdict: str            # pending / error; 组内标准化后填 builtin/not_builtin/inconclusive
    detail: dict


def _entropy_from_top(top: dict | None, lp: float | None) -> float:
    if top:
        lps = list(top.values())
        mx = max(lps)
        ps = [math.exp(x - mx) for x in lps]
        s = sum(ps) or 1.0
        ps = [p / s for p in ps]
        return -sum(p * math.log(p) for p in ps if p > 0)
    return abs(lp) if lp is not None else 0.0


# ---------------------------------------------------------------- S1
def s1_decode_entropy(cfg, mat, task_prompt: str, max_tokens: int = 64) -> SchemeResult:
    prompt = (f"Task: {task_prompt}\n\n"
              "Briefly outline your step-by-step execution plan:\n")
    r = completion_logprobs(cfg, prompt, max_tokens=max_tokens, topk=10)
    ents = [_entropy_from_top(t, lp) for t, lp in zip(r["top"], r["logprobs"]) if lp is not None]
    mean_e = sum(ents) / len(ents) if ents else 99.0
    return SchemeResult("S1_decode_entropy", mat.task_id, score=-mean_e, verdict="pending",
                        detail={"mean_entropy": round(mean_e, 4), "n_tokens": len(ents)})


# ---------------------------------------------------------------- S2
def s2_nll_counterfactual(cfg, mat, cf_trace: str) -> SchemeResult:
    sep = "\n\n```\n"
    tail = "\n```"
    res = {}
    for label, trace in (("gold", mat.gold_trace), ("cf", cf_trace)):
        text = f"Task: {mat.task_text}\nSolution skeleton:{sep}{trace}{tail}"
        pl = prompt_logprobs(cfg, text, topk=5)
        s_idx = _char_to_token_idx(cfg, text, len(text) - len(trace) - len(tail))
        lps = pl["logprobs"][s_idx:]
        _, all_nll = _seq_nll(lps)
        res[label] = {"nll": all_nll, "n": len(lps)}
    delta = res["cf"]["nll"] - res["gold"]["nll"]
    return SchemeResult("S2_nll_counterfactual", mat.task_id, score=delta, verdict="pending",
                        detail={"gold": res["gold"], "cf": res["cf"], "delta_nll": round(delta, 4)})


# ---------------------------------------------------------------- S3
def s3_context_dependency(cfg, mat) -> SchemeResult:
    tail = "\n```"
    res = {}
    for label, use_ctx in (("with_skill", True), ("no_skill", False)):
        ctx = f"\n\n[Reference]\n{mat.skill_md}\n" if use_ctx else ""
        text = f"Task: {mat.task_text}{ctx}\nSolution skeleton:\n```\n{mat.gold_trace}{tail}"
        pl = prompt_logprobs(cfg, text, topk=0)
        s_idx = _char_to_token_idx(cfg, text, len(text) - len(mat.gold_trace) - len(tail))
        lps = pl["logprobs"][s_idx:]
        _, all_nll = _seq_nll(lps)
        res[label] = {"nll": all_nll, "n": len(lps)}
    delta = res["no_skill"]["nll"] - res["with_skill"]["nll"]
    return SchemeResult("S3_context_dependency", mat.task_id, score=-delta, verdict="pending",
                        detail={"with_skill": res["with_skill"], "no_skill": res["no_skill"],
                                "delta_nll": round(delta, 4)})


# ---------------------------------------------------------------- S4
def s4_toolname_probe(cfg, mat, all_tools: dict[str, list[str]]) -> SchemeResult:
    prompt = (f"Task: {mat.task_text}\n\n"
              "The most appropriate specialized tool/library for this task is")
    r = completion_logprobs(cfg, prompt, max_tokens=6, topk=20)
    cand_text = " ".join(r["top"][0].keys()) if r["top"] else ""
    own_names = [w for w in mat.identifiers if w[0].isupper()][:2] or [mat.skill_name]
    own_lp = None
    for nm in own_names:
        lp = _first_token_lp(r, nm)
        if lp is not None:
            own_lp = lp
            break
    others = {}
    for tid, tools in all_tools.items():
        if tid == mat.task_id:
            continue
        for t in tools[:1]:
            lp = _first_token_lp(r, t)
            if lp is not None:
                others[t] = lp
    if own_lp is None:
        score = -8.0
    elif others:
        score = own_lp - (sum(others.values()) / len(others))
    else:
        score = -8.0
    return SchemeResult("S4_toolname_probe", mat.task_id, score=score, verdict="pending",
                        detail={"own_probe_names": own_names, "own_first_token_lp": own_lp,
                                "topk_head": cand_text[:150],
                                "others_mean_lp": (sum(others.values()) / len(others)) if others else None})


def _first_token_lp(r: dict, tool_name: str) -> float | None:
    if not r["top"]:
        return None
    first_tok = tool_name.split()[0].split(",")[0]
    tl = first_tok.lower()
    for cand, lp in r["top"][0].items():
        c = cand.strip().lstrip("(").lower()
        if not c:
            continue
        if c == tl or c.startswith(tl) or tl.startswith(c):
            return lp
    return None


# ---------------------------------------------------------------- S5 (单行打分, 矩阵在 run_all 编排)
def s5_score_cell(cfg, prompt_task_text: str, trace: str) -> float:
    """NLL[i][j] 单元格: 任务 i 的 prompt 对轨迹 j 的 trace 段平均 NLL。"""
    text = f"Task: {prompt_task_text}\nSolution skeleton:\n```\n{trace}\n```"
    pl = prompt_logprobs(cfg, text, topk=0)
    s_idx = _char_to_token_idx(cfg, text, len(text) - len(trace) - 4)
    lps = [x for x in pl["logprobs"][s_idx:] if x is not None]
    return -sum(lps) / len(lps) if lps else 99.0


# ---------------------------------------------------------------- helpers
def _char_to_token_idx(cfg, text: str, char_pos: int) -> int:
    return len(tokenize(cfg, text[:char_pos]))


def _seq_nll(lps: list) -> tuple[float, float]:
    valid = [lp for lp in lps if lp is not None]
    if not valid:
        return 99.0, 99.0
    nll = -sum(valid) / len(valid)
    return nll, nll
