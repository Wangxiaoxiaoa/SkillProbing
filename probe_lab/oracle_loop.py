"""skillsbench oracle 全量校准: 逐任务官方单任务模式, 6 并发, 断点续跑。"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
SB = Path("/data1/xiao/skillsbench")
RUNS = PROJ / "outputs" / "bench_runs" / "oracle_loop"
_lock = threading.Lock()


def log(m):
    with _lock:
        print(m, flush=True)


def run_one(task: str) -> dict:
    jobs = RUNS / task
    cmd = (f"uv run bench eval run --tasks-dir tasks/{task} "
           f"--agent oracle --sandbox docker --jobs-dir {jobs}")
    t0 = time.time()
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                           timeout=3600, cwd=str(SB), env={**dict(__import__("os").environ),
                                                           "PATH": "/home/uos/.local/bin:" + __import__("os").environ["PATH"]})
        log_txt = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        log_txt = "TIMEOUT"
    dt = time.time() - t0
    # 解析 score
    passed = failed = errored = total = 0
    summaries = sorted(RUNS.glob(f"{task}/*/summary.json"))
    if summaries:
        try:
            s = json.loads(open(summaries[-1]).read())
            total, passed, failed, errored = (s.get("total", 0), s.get("passed", 0),
                                              s.get("failed", 0), s.get("errored", 0))
        except Exception:
            pass
    verdict = ("PASS" if passed == total and total > 0 else
               ("ERRORED" if errored == total else "FAIL"))
    return {"task": task, "verdict": verdict, "passed": passed, "failed": failed,
            "errored": errored, "total": total, "elapsed": round(dt)}


def main():
    tasks = sorted(p.name for p in (SB / "tasks").iterdir() if p.is_dir())
    out_path = RUNS / "oracle_loop.json"
    RUNS.mkdir(parents=True, exist_ok=True)
    done = json.load(open(out_path)) if out_path.exists() else {}
    todo = [t for t in tasks if done.get(t, {}).get("verdict") != "PASS"]
    log(f"[plan] oracle 校准 {len(todo)}/{len(tasks)} 任务, 6 并发")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(run_one, t): t for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"task": t, "verdict": "ERROR", "detail": str(e)[:120]}
            done[t] = r
            log(f"[{n}/{len(todo)}] {t}: {r['verdict']} ({r.get('elapsed', 0)}s)")
            json.dump(done, open(out_path, "w"), indent=1, ensure_ascii=False)
    ok = sum(1 for r in done.values() if r.get("verdict") == "PASS")
    log(f"\n[done] oracle 校准通过 {ok}/{len(done)} -> {out_path}")


if __name__ == "__main__":
    main()
