"""SGLang logprobs 客户端 (glm53flash@5919)。

1. prompt-side logprobs: /generate + return_logprob + logprob_start_len=0
2. 生成侧 top-k logprobs: /v1/completions + logprobs=k
3. 服务端分词: /tokenize
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass
class SGLangConfig:
    base_url: str = "http://127.0.0.1:5919"
    api_key: str = "Tongxin123"
    model: str = "glm53flash"
    timeout: int = 300


def _post(cfg: SGLangConfig, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        cfg.base_url.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        return json.loads(r.read())


def tokenize(cfg: SGLangConfig, text: str) -> list[int]:
    d = _post(cfg, "/tokenize", {"prompt": text})
    return d.get("tokens", [])


def prompt_logprobs(cfg: SGLangConfig, text: str, topk: int = 0) -> dict:
    """teacher-forcing: 返回每个 prompt token 的 logprob。logprobs[0]=None。"""
    payload = {
        "text": text,
        "sampling_params": {"max_new_tokens": 1, "temperature": 0},
        "return_logprob": True,
        "logprob_start_len": 0,
        "top_logprobs_num": max(topk, 1),
    }
    d = _post(cfg, "/generate", payload)
    meta = d["meta_info"]
    ipl = meta.get("input_token_logprobs") or []
    itop = meta.get("input_top_logprobs") or []
    token_ids, lps, tops = [], [], []
    for i, item in enumerate(ipl):
        lp, tid = item[0], item[1]
        token_ids.append(tid)
        lps.append(lp)
        t = None
        if i < len(itop) and itop[i]:
            t = {x[1]: x[0] for x in itop[i]}
        tops.append(t)
    return {"token_ids": token_ids, "logprobs": lps, "top": tops,
            "prompt_tokens": meta.get("prompt_tokens")}


def completion_logprobs(cfg: SGLangConfig, prompt: str, max_tokens: int = 32,
                        topk: int = 10) -> dict:
    d = _post(cfg, "/v1/completions", {
        "model": cfg.model, "prompt": prompt, "max_tokens": max_tokens,
        "temperature": 0, "logprobs": topk,
    })
    ch = d["choices"][0]
    lp = ch.get("logprobs") or {}
    return {"tokens": lp.get("tokens") or [], "logprobs": lp.get("token_logprobs") or [],
            "top": lp.get("top_logprobs") or [], "text": ch.get("text", "")}
