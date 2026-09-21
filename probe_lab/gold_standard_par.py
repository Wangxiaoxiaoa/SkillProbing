"""并行版金标准: 线程池并发跑 (任务 × with/without) 执行单元。

GT 判定: without pass → BUILTIN; with pass+without fail → NOT_BUILTIN; 双 fail → ABSENT
用法:
  python3 gold_standard_par.py --workers 16                  # 全量
  python3 gold_standard_par.py --tasks a b --workers 4       # 指定任务(断点续跑)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
SB = Path("/data1/xiao/skillsbench")          # skillsbench 原始仓库
sys.path.insert(0, str(PROJ / "src"))

from skillprobing.skills.skill_loader import load_task_dir
from skillprobing.run.agent import build_prompt
from skillprobing.tasks.verifier import run_verifier

OUT = PROJ / "outputs" / "gold_standard"
OPENCODE = "/home/uos/node-v22.23.0-linux-x64/bin/opencode"
MODEL = "tongxin/glm53flash"

ENV_NOTE = ("\n\n[环境说明] 本机无容器。你的工作目录是: {ws}\n"
            "任务描述中所有要求写入 /root/、/app/、/workspace/、/output/ 的文件,"
            "一律写入上述工作目录(文件名不变)。工作目录中已提供任务输入文件。"
            "请高效完成任务,避免不必要的探索。")

_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def run_opencode(prompt: str, ws: Path, timeout: int) -> tuple[int, str]:
    ws.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = "/home/uos/node-v22.23.0-linux-x64/bin:" + env.get("PATH", "")
    cmd = [OPENCODE, "run", "--model", MODEL, prompt]
    try:
        r = subprocess.run(cmd, cwd=str(ws), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT after {timeout}s"


def run_unit(task_name: str, with_skill: bool, timeout: int) -> dict:
    task_dir = SB / "tasks" / task_name
    if not task_dir.is_dir():
        task_dir = SB / "tasks-extra" / task_name
    task = load_task_dir(task_dir)
    label = "with_skill" if with_skill else "without_skill"
    out_dir = OUT / task.task_id / label
    ws = out_dir / "workspace"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)

    prompt = build_prompt(task.prompt, task.skills_dir if with_skill else None)
    prompt = prompt + ENV_NOTE.format(ws=ws.resolve())
    t0 = time.time()
    rc, logtxt = run_opencode(prompt, ws, timeout)
    dt = time.time() - t0
    (out_dir / "opencode.log").write_text(logtxt[-20000:], encoding="utf-8")
    v = run_verifier(task_dir, ws)
    res = {"task_id": task.task_id, "condition": label, "rc": rc,
           "passed": v.passed, "score": v.score,
           "detail": v.detail[-300:], "elapsed": round(dt, 1)}
    log(f"  [{label:<13}] {task.task_id:<44} passed={v.passed} ({dt:.0f}s)")
    return res


def gt_of(w, wo):
    if wo["passed"]:
        return "BUILTIN"
    if w["passed"]:
        return "NOT_BUILTIN"
    return "ABSENT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    all_tasks = sorted(p.name for p in (SB / "tasks").iterdir() if p.is_dir())
    extra = sorted(p.name for p in (SB / "tasks-extra").iterdir() if p.is_dir())
    todo = all_tasks + [t for t in extra if t not in set(all_tasks)]
    if args.tasks:
        todo = args.tasks
    if args.smoke:
        todo = todo[:3]

    gt_path = OUT / "gt.json"
    gt = json.load(open(gt_path)) if gt_path.exists() else {}
    todo = [t for t in todo if t not in gt or "verdict" not in gt.get(t, {})]
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"[plan] 待跑 {len(todo)} 任务 × 2 condition, workers={args.workers}")

    units = [(t, True) for t in todo] + [(t, False) for t in todo]
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_unit, t, w, args.timeout): (t, w) for t, w in units}
        done = 0
        for fut in as_completed(futs):
            t, w = futs[fut]
            done += 1
            try:
                results[(t, w)] = fut.result()
            except Exception as e:
                log(f"  [ERROR] {t} {w}: {str(e)[:150]}")
                results[(t, w)] = {"task_id": t, "condition": "with_skill" if w else "without_skill",
                                   "passed": False, "score": 0.0,
                                   "detail": f"error: {str(e)[:200]}", "elapsed": 0}
            if done % 20 == 0:
                log(f"[progress] {done}/{len(units)} units done")

    for t in todo:
        w = results.get((t, True), {"passed": False, "detail": "missing"})
        wo = results.get((t, False), {"passed": False, "detail": "missing"})
        gt[t] = {"with": w, "without": wo, "verdict": gt_of(w, wo)}
    json.dump(gt, open(gt_path, "w"), ensure_ascii=False, indent=2)
    from collections import Counter
    c = Counter(v["verdict"] for v in gt.values())
    log(f"\n[done] GT 总量 {len(gt)}: {dict(c)} -> {gt_path}")


if __name__ == "__main__":
    main()
