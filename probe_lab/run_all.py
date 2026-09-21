"""全量探测: S1-S4 按任务并发; S5 构建全量 N×N 矩阵(单元格并发) + 双中心化。

用法:
  python3 run_all.py --workers 12            # 101 任务全量
  python3 run_all.py --tasks a b --quick     # 冒烟
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

sys.path.insert(0, str(Path(__file__).parent))

from sglang_client import SGLangConfig
from materials import build_materials, global_identifier_pool, make_counterfactual
from schemes import (s1_decode_entropy, s2_nll_counterfactual, s3_context_dependency,
                     s4_toolname_probe, s5_score_cell, SchemeResult)

OUT = Path("/data1/xiao/SkillProbing/outputs/probe_lab")


def _truncate(text: str, n: int = 1500) -> str:
    return re.sub(r"\s+", " ", text).strip()[:n]


def summarize(results: list[SchemeResult]) -> dict:
    by_scheme: dict[str, list[SchemeResult]] = {}
    for r in results:
        by_scheme.setdefault(r.scheme, []).append(r)
    out = {}
    for scheme, rs in by_scheme.items():
        scores = [r.score for r in rs]
        mu = statistics.mean(scores) if scores else 0
        sd = statistics.pstdev(scores) or 1.0
        rows = []
        for r in rs:
            z = (r.score - mu) / sd
            verdict = "builtin" if z > 0.5 else ("not_builtin" if z < -0.5 else "inconclusive")
            if r.verdict == "error":
                verdict = "error"
            rows.append({"task_id": r.task_id, "score": round(r.score, 4),
                         "z": round(z, 3), "verdict": verdict})
        out[scheme] = rows
    return out


def s5_matrix_parallel(cfg, mats, workers: int) -> dict:
    """N×N 打分矩阵, 单元格级并发。返回 {task_i: {task_j: nll}}。"""
    cells = [(i, j) for i, m in enumerate(mats) for j, _ in enumerate(mats)]
    nll_flat = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(s5_score_cell, cfg, _truncate(mats[i].task_text, 1200),
                            mats[j].gold_trace): (i, j) for i, j in cells}
        for n, fut in enumerate(as_completed(futs), 1):
            i, j = futs[fut]
            try:
                nll_flat[(i, j)] = fut.result()
            except Exception as e:
                nll_flat[(i, j)] = 99.0
            if n % 500 == 0:
                log(f"  [S5] {n}/{len(cells)} cells ({time.time()-t0:.0f}s)", lock=False)
    N = {m.task_id: {} for m in mats}
    id_by_i = [m.task_id for m in mats]
    for (i, j), v in nll_flat.items():
        N[id_by_i[i]][id_by_i[j]] = round(v, 4)
    return N


_print_lock = None
def log(msg: str, lock: bool = True):
    if lock and _print_lock:
        with _print_lock:
            print(msg, flush=True)
    else:
        print(msg, flush=True)


def main():
    global _print_lock
    import threading
    _print_lock = threading.Lock()

    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--skip-s5", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cfg = SGLangConfig()
    mats = build_materials()          # skillsbench 全部任务
    if args.tasks:
        mats = [m for m in mats if m.task_id in args.tasks]
    print(f"[load] {len(mats)} tasks", flush=True)

    pool_ids = global_identifier_pool(mats)
    rng = random.Random(42)
    all_tools = {m.task_id: (m.identifiers[:3] or [m.skill_name]) for m in mats}
    cfs = {m.task_id: make_counterfactual(m.gold_trace, m.key_spans, pool_ids, rng) for m in mats}
    (OUT / "materials_preview.json").write_text(json.dumps([{
        "task_id": m.task_id, "skill": m.skill_name,
        "trace_head": m.gold_trace[:120], "n_identifiers": len(m.identifiers),
    } for m in mats], ensure_ascii=False, indent=2), encoding="utf-8")

    results: list[SchemeResult] = []
    t0 = time.time()

    # ---- S1-S4: 任务级并发 ----
    def one_task(m):
        tp = _truncate(m.task_text, 1200)
        out = []
        for fn, kwargs in [
            (s1_decode_entropy, dict(mat=m, task_prompt=tp)),
            (s2_nll_counterfactual, dict(mat=m, cf_trace=cfs[m.task_id])),
            (s3_context_dependency, dict(mat=m)),
            (s4_toolname_probe, dict(mat=m, all_tools=all_tools)),
        ]:
            try:
                out.append(fn(cfg, **kwargs))
            except Exception as e:
                out.append(SchemeResult(fn.__name__, m.task_id, 0.0, "error",
                                        {"error": str(e)[:200]}))
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(one_task, m): m for m in mats}
        for n, fut in enumerate(as_completed(futs), 1):
            rs = fut.result()
            results.extend(rs)
            log(f"[{n}/{len(mats)}] {futs[fut].task_id} S1-S4 done ({time.time()-t0:.0f}s)")

    # ---- S5: N×N 矩阵 ----
    if not args.skip_s5:
        print(f"[S5] building {len(mats)}x{len(mats)} matrix...", flush=True)
        N = s5_matrix_parallel(cfg, mats, workers=args.workers)
        for m in mats:
            own = N[m.task_id][m.task_id]
            off = [v for k, v in N[m.task_id].items() if k != m.task_id]
            margin = statistics.mean(off) - own if off else 0.0
            best = min(N[m.task_id], key=N[m.task_id].get)
            results.append(SchemeResult(
                "S5_cross_matching", m.task_id, score=margin, verdict="pending",
                detail={"own_nll": round(own, 4), "best_match": best,
                        "hit": best == m.task_id,
                        "nll_matrix": N[m.task_id]}))
        # 双中心化
        adj_summary = s5_adjust(mats, N)
        (OUT / "s5_adjusted.json").write_text(json.dumps(adj_summary, ensure_ascii=False, indent=2),
                                              encoding="utf-8")

    verdicts = summarize(results)
    (OUT / "results.json").write_text(json.dumps(
        {"results": [r.__dict__ | {"detail": r.detail} for r in results],
         "verdicts": verdicts}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[done] {time.time()-t0:.0f}s -> {OUT}/results.json", flush=True)


def s5_adjust(mats, N: dict) -> dict:
    """双中心化: adj[i][j] = N[i][j] - mean_k(N[k][j])。"""
    tasks = [m.task_id for m in mats]
    col_mean = {j: statistics.mean(N[i][j] for i in tasks) for j in tasks}
    adj = {i: {j: N[i][j] - col_mean[j] for j in tasks} for i in tasks}
    out = {}
    for i in tasks:
        own = adj[i][i]
        off = [adj[i][j] for j in tasks if j != i]
        margin = statistics.mean(off) - own
        out[i] = {"adj_margin": round(margin, 4),
                  "best_match": min(adj[i], key=adj[i].get),
                  "hit": min(adj[i], key=adj[i].get) == i}
    hits = sum(1 for v in out.values() if v["hit"])
    margins = [v["adj_margin"] for v in out.values()]
    mu, sd = statistics.mean(margins), statistics.pstdev(margins) or 1.0
    return {"per_task": out, "hits": hits, "n": len(tasks),
            "margin_mean": round(mu, 4), "margin_std": round(sd, 4),
            "frac_positive": round(sum(1 for x in margins if x > 0) / len(margins), 3),
            "group_t": round(mu / (sd / len(margins) ** 0.5), 2)}


if __name__ == "__main__":
    main()
