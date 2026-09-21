"""BFCL 全量金标准对照: 1493 条 × with/without skill 单轮调用 + 简化判分。

skill = 候选函数 docstring 组合(探测物料一致)。
GT 定义: without pass → BUILTIN; with pass+without fail → NOT_BUILTIN; 双 fail → ABSENT
输出: outputs/probes/bfcl_gold.json
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
sys.path.insert(0, str(PROJ / "probe_lab"))
sys.path.insert(0, str(PROJ / "probe_lab" / "adapters"))

from sglang_client import SGLangConfig, _post
from adapters import get_adapter

OUT = Path(PROJ / "outputs" / "probes")
_print_lock = threading.Lock()


def log(m):
    with _print_lock:
        print(m, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    cfg = SGLangConfig()
    ad = get_adapter("bfcl")
    tasks = ad.list_tasks()
    if args.limit:
        tasks = tasks[:args.limit]

    out_path = OUT / "bfcl_gold.json"
    results = json.load(open(out_path)) if out_path.exists() else {}
    todo = [t for t in tasks if t not in results]
    log(f"[plan] {len(todo)} 条 × 2 条件, workers={args.workers}")

    def llm_call(prompt: str) -> str:
        d = _post(cfg, "/v1/chat/completions", {
            "model": "glm53flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400, "temperature": 0})
        return d["choices"][0]["message"]["content"]

    def unit(task_id: str, with_skill: bool):
        try:
            v = ad.run_gold(task_id, with_skill=with_skill, llm_call=llm_call)
            return task_id, {"with_skill" if with_skill else "without_skill":
                             {"passed": v.passed, "detail": v.detail}}
        except Exception as e:
            return task_id, {"with_skill" if with_skill else "without_skill":
                             {"passed": False, "detail": f"error: {str(e)[:120]}"}}

    units = [(t, True) for t in todo] + [(t, False) for t in todo]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(unit, t, w): t for t, w in units}
        for n, fut in enumerate(as_completed(futs), 1):
            tid, res = fut.result()
            results.setdefault(tid, {}).update(res)
            if n % 100 == 0:
                wp = sum(1 for v in results.values() if v.get("with_skill", {}).get("passed"))
                wop = sum(1 for v in results.values() if v.get("without_skill", {}).get("passed"))
                log(f"[{n}/{len(units)}] with_pass={wp} without_pass={wop} ({time.time()-t0:.0f}s)")
                json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1)

    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1)
    wp = sum(1 for v in results.values() if v.get("with_skill", {}).get("passed"))
    wop = sum(1 for v in results.values() if v.get("without_skill", {}).get("passed"))
    n = len(results)
    log(f"\n[done] {n} 条: with_skill 通过 {wp} ({wp/n*100:.1f}%), "
        f"without 通过 {wop} ({wop/n*100:.1f}%), skill 增益 Δ={(wp-wop)/n*100:+.1f}pp")


if __name__ == "__main__":
    main()
