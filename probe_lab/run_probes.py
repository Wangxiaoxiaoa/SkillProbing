"""统一多 benchmark 探测 runner: 任意适配器跑 S1-S4, S5 用采样子矩阵。

用法:
  python3 run_probes.py --benchmark ds1000 --limit 50 --workers 12
  python3 run_probes.py --benchmark bfcl --limit 100
  python3 run_probes.py --benchmark ds1000 --s5-sample 40
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
sys.path.insert(0, str(PROJ / "probe_lab"))
sys.path.insert(0, str(PROJ / "probe_lab" / "adapters"))

from sglang_client import SGLangConfig
from adapters import get_adapter
from schemes import (s1_decode_entropy, s2_nll_counterfactual, s3_context_dependency,
                     s4_toolname_probe, s5_score_cell, SchemeResult)

OUT = PROJ / "outputs" / "probes"
_counter_lock = __import__("threading").Lock()


def _truncate(text: str, n: int = 1200) -> str:
    return re.sub(r"\s+", " ", text).strip()[:n]


def summarize(results: list[SchemeResult]) -> dict:
    by = {}
    for r in results:
        by.setdefault(r.scheme, []).append(r)
    out = {}
    for scheme, rs in by.items():
        scores = [r.score for r in rs]
        mu = statistics.mean(scores) if scores else 0
        sd = statistics.pstdev(scores) or 1.0
        out[scheme] = [{"task_id": r.task_id, "score": round(r.score, 4),
                        "z": round((r.score - mu) / sd, 3),
                        "verdict": ("builtin" if (r.score - mu) / sd > 0.5 else
                                    ("not_builtin" if (r.score - mu) / sd < -0.5 else
                                     "inconclusive")) if r.verdict != "error" else "error"}
                       for r in rs]
    return out


def s5_sample_matrix(cfg, mats, sample: int, workers: int) -> dict:
    """S5 采样子矩阵: 随机抽 sample 个任务做 N×N。"""
    rng = random.Random(0)
    idx = rng.sample(range(len(mats)), min(sample, len(mats)))
    sub = [mats[i] for i in idx]
    cells = [(i, j) for i in range(len(sub)) for j in range(len(sub))]
    flat = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(s5_score_cell, cfg, _truncate(sub[i].task_text),
                            sub[j].gold_trace): (i, j) for i, j in cells}
        for n, fut in enumerate(as_completed(futs), 1):
            i, j = futs[fut]
            try:
                flat[(i, j)] = fut.result()
            except Exception:
                flat[(i, j)] = 99.0
            if n % 200 == 0:
                print(f"  [S5] {n}/{len(cells)} ({time.time()-t0:.0f}s)", flush=True)
    N = {m.task_id: {} for m in sub}
    ids = [m.task_id for m in sub]
    for (i, j), v in flat.items():
        N[ids[i]][ids[j]] = round(v, 4)
    # 双中心化
    col = {j: statistics.mean(N[i][j] for i in tasks) for j, tasks in [(j, [i for i in ids]) for j in ids]}
    adj = {i: {j: N[i][j] - col[j] for j in ids} for i in ids}
    per, hits, margins = {}, 0, []
    for i in ids:
        own = adj[i][i]
        off = [adj[i][j] for j in ids if j != i]
        margin = statistics.mean(off) - own
        best = min(adj[i], key=adj[i].get)
        hits += best == i
        margins.append(margin)
        per[i] = {"adj_margin": round(margin, 4), "best_match": best, "hit": best == i}
    mu, sd = statistics.mean(margins), statistics.pstdev(margins) or 1.0
    return {"per_task": per, "hits": hits, "n": len(ids),
            "frac_positive": round(sum(1 for x in margins if x > 0) / len(margins), 3),
            "group_t": round(mu / (sd / len(ids) ** 0.5), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", required=True,
                    choices=["ds1000", "bfcl", "taubench", "kernelbench", "terminalbench"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--s5-sample", type=int, default=0, help="S5 采样子矩阵大小(0=跳过S5)")
    ap.add_argument("--skip-s1234", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cfg = SGLangConfig()
    ad = get_adapter(args.benchmark)
    mats = ad.load_all_materials(limit=args.limit)
    mats = [m for m in mats if m.gold_trace.strip()]
    print(f"[{args.benchmark}] {len(mats)} 任务(含 gold trace)", flush=True)

    rng = random.Random(42)
    pool_ids = sorted({i for m in mats for i in m.identifiers})
    cfs = {m.task_id: __import__("materials").make_counterfactual(
        m.gold_trace, m.key_spans, pool_ids, rng) for m in mats}
    all_tools = {m.task_id: (m.identifiers[:3] or [m.skill_name]) for m in mats}

    results: list[SchemeResult] = []
    t0 = time.time()

    if not args.skip_s1234:
        def one_task(m):
            out = []
            tp = _truncate(m.task_text)
            cf = cfs[m.task_id]
            for fn, kw in [
                (s1_decode_entropy, dict(mat=m, task_prompt=tp)),
                (s2_nll_counterfactual, dict(mat=m, cf_trace=cf)),
                (s3_context_dependency, dict(mat=m)),
                (s4_toolname_probe, dict(mat=m, all_tools=all_tools)),
            ]:
                try:
                    out.append(fn(cfg, **kw))
                except Exception as e:
                    out.append(SchemeResult(fn.__name__, m.task_id, 0.0, "error",
                                            {"error": str(e)[:150]}))
            return out

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(one_task, m): m for m in mats}
            for n, fut in enumerate(as_completed(futs), 1):
                rs = fut.result()
                results.extend(rs)
                with _counter_lock:
                    print(f"[{n}/{len(mats)}] {futs[fut].task_id} ({time.time()-t0:.0f}s)", flush=True)

    if args.s5_sample:
        print(f"[S5] {args.s5_sample}x{args.s5_sample} 采样矩阵...", flush=True)
        s5res = s5_sample_matrix(cfg, mats, args.s5_sample, args.workers)
        for tid, p in s5res["per_task"].items():
            results.append(SchemeResult("S5_cross_matching", tid, p["adj_margin"],
                                        "pending", {**p, "_summary": {
                                            k: v for k, v in s5res.items() if k != "per_task"}}))
        print(f"  S5: 命中 {s5res['hits']}/{s5res['n']}, 正比例 {s5res['frac_positive']}, t={s5res['group_t']}")

    out_path = OUT / f"{args.benchmark}_probes.json"
    (OUT / f"{args.benchmark}_probes.json").write_text(json.dumps(
        {"benchmark": args.benchmark, "n_tasks": len(mats),
         "results": [r.__dict__ | {"detail": r.detail} for r in results],
         "verdicts": summarize(results)}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(f"\n[done] {time.time()-t0:.0f}s -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
