"""从网关/推理响应中提取每个 token 的 logprobs(用于熵分析)。

网关截获的是 OpenAI 兼容响应;
- 非流式: choices[].logprobs.content = [{token, logprob, top_logprobs: [{logprob,...},...]}, ...]
- 若是 raw SSE: 需解析拼接出同样的 logprobs 结构。

输出统一结构: 每个生成 token 一个
  { token_id_or_text, logprob (float), top: [(token,logprob), ...] }
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class TokenLogprob:
    token: str
    logprob: float
    top: list[tuple[Any, float]]  # topK (token, logprob)


def extract_token_logprobs(resp: dict[str, Any]) -> list[TokenLogprob]:
    """从一次 chat.completion 响应的 logprobs 提取每个生成 token 的分布。"""
    out: list[TokenLogprob] = []
    choices = (resp or {}).get("choices") or []
    if not choices:
        return out
    lp = choices[0].get("logprobs")
    if lp is None:
        return out
    content = lp.get("content") or []
    for item in content:
        if not isinstance(item, dict):
            continue
        token = item.get("token", "")
        logprob = item.get("logprob", 0.0)
        top = []
        for t in item.get("top_logprobs") or []:
            if isinstance(t, dict):
                top.append((t.get("token"), float(t.get("logprob", 0.0))))
        out.append(TokenLogprob(token=token, logprob=logprob, top=top))
    return out


def load_trace_logprobs(trace_file: str) -> list[list[TokenLogprob]]:
    """从网关轨迹 jsonl 提取每次调用的 per-token logprobs。

    trace_file 每行 = 一次 LLM 调用记录 (含 response)。
    返回: 每次调用一个 list[TokenLogprob]。
    """
    per_call: list[list[TokenLogprob]] = []
    with open(trace_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = rec.get("response")
            if isinstance(resp, dict):
                toks = extract_token_logprobs(resp)
                if toks:
                    per_call.append(toks)
    return per_call