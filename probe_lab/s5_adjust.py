"""S5 双中心化: adj[i][j] = NLL[i][j] - mean_k(NLL[k][j]), 消除轨迹自身流畅度主效应。"""
import json, statistics, sys
from pathlib import Path
OUT = Path("/data1/xiao/SkillProbing/outputs/probe_lab")

def analyze(results_path=OUT / "results.json"):
    d = json.load(open(results_path))
    s5 = [r for r in d["results"] if r["scheme"] == "S5_cross_matching"]
    if not s5:
        print("无 S5 数据"); return None
    tasks = [r["task_id"] for r in s5]
    N = {r["task_id"]: r["detail"]["nll_matrix"] for r in s5}
    col_mean = {j: statistics.mean(N[i][j] for i in tasks) for j in tasks}
    adj = {i: {j: N[i][j] - col_mean[j] for j in tasks} for i in tasks}
    out = {}
    for i in tasks:
        own = adj[i][i]
        off = [adj[i][j] for j in tasks if j != i]
        out[i] = {"adj_margin": round(statistics.mean(off) - own, 4),
                  "best_match": min(adj[i], key=adj[i].get),
                  "hit": min(adj[i], key=adj[i].get) == i}
    hits = sum(1 for v in out.values() if v["hit"])
    margins = [v["adj_margin"] for v in out.values()]
    mu, sd = statistics.mean(margins), statistics.pstdev(margins) or 1.0
    summary = {"per_task": out, "hits": hits, "n": len(tasks),
               "margin_mean": round(mu, 4), "margin_std": round(sd, 4),
               "frac_positive": round(sum(1 for x in margins if x > 0) / len(margins), 3),
               "group_t": round(mu / (sd / len(margins) ** 0.5), 2)}
    json.dump(summary, open(OUT / "s5_adjusted.json", "w"), indent=2, ensure_ascii=False)
    print(f"S5(双中心化): 命中 {hits}/{len(tasks)}, margin mean={mu:.3f}, 组级t={summary['group_t']}")
    return summary

if __name__ == "__main__":
    analyze()
