"""预构建 skillsbench 全部任务镜像(3路并发, 断点续跑)。

原因: 本机到 apt 源慢, bench 内部 build+run 超时(613s)会静默掐掉任务。
预构建后镜像层缓存, bench eval 的 build 步骤秒过。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SB = Path("/data1/xiao/skillsbench")
OUT = Path("/data1/xiao/SkillProbing/outputs/prebuild.json")
_lock = threading.Lock()


def log(m):
    with _lock:
        print(m, flush=True)


def build_one(task: str) -> dict:
    env_dir = SB / "tasks" / task / "environment"
    img = f"sb-pre/{task}"
    t0 = time.time()
    try:
        r = subprocess.run(
            ["sudo", "docker", "build", "--network", "host",
             "--build-arg", "HTTP_PROXY=http://172.17.0.1:18080",
             "--build-arg", "HTTPS_PROXY=http://172.17.0.1:18080",
             "--build-arg", "http_proxy=http://172.17.0.1:18080",
             "--build-arg", "https_proxy=http://172.17.0.1:18080",
             "-t", img, "."],
            capture_output=True, text=True, timeout=5400, cwd=str(env_dir))
        ok = r.returncode == 0
        detail = "ok" if ok else r.stderr[-250:]
    except subprocess.TimeoutExpired:
        ok, detail = False, "build超时(>90min)"
    dt = time.time() - t0
    log(f"[build] {task}: {'✅' if ok else '❌'} ({dt:.0f}s)")
    return {"task": task, "ok": ok, "elapsed": round(dt), "detail": detail}


def main():
    tasks = sorted(p.name for p in (SB / "tasks").iterdir() if (p / "environment" / "Dockerfile").exists())
    done = json.load(open(OUT)) if OUT.exists() else {}
    todo = [t for t in tasks if not done.get(t, {}).get("ok")]
    log(f"[plan] 预构建 {len(todo)}/{len(tasks)} 任务镜像, 3 路并发")
    results = dict(done)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(build_one, t): t for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"task": t, "ok": False, "elapsed": 0, "detail": str(e)[:150]}
            results[t] = r
            json.dump(results, open(OUT, "w"), indent=1, ensure_ascii=False)
    ok = sum(1 for r in results.values() if r["ok"])
    log(f"[done] 镜像就绪 {ok}/{len(results)}")


if __name__ == "__main__":
    main()
