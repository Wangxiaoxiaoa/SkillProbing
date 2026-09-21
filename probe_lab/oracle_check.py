"""oracle 校验: 把 skillsbench 官方参考解法(oracle/solve.sh)在 workspace 里执行,
再用 verifier 判分。官方答案都 fail 的任务 = verifier 本身坏(环境适配问题)。

用法: python3 oracle_check.py [--tasks ...] [--timeout 900]
输出: outputs/gold_standard/oracle_check.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
SB = Path("/data1/xiao/skillsbench")
sys.path.insert(0, str(PROJ / "src"))
from skillprobing.tasks.verifier import run_verifier

OUT = Path("/data1/xiao/SkillProbing/outputs/gold_standard")
_lock = __import__("threading").Lock()


def log(m):
    with _lock:
        print(m, flush=True)


def check_one(task_name: str, timeout: int) -> dict:
    """跑 oracle solve.sh -> verifier 判分。"""
    task_dir = SB / "tasks" / task_name
    if not task_dir.is_dir():
        task_dir = SB / "tasks-extra" / task_name
    solve = task_dir / "oracle" / "solve.sh"
    ws = OUT / "_oracle_check" / task_name / "workspace"
    if ws.parent.exists():
        shutil.rmtree(ws.parent, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)

    result = {"task_id": task_name, "oracle_ok": None, "verifier_passed": None,
              "detail": "", "solve_rc": None, "elapsed": 0}
    if not solve.exists():
        result["detail"] = "无 oracle/solve.sh"
        result["oracle_ok"] = False
        return result

    # 完整拷贝 environment(含子目录数据, 如 DATA/), 供 oracle 脚本读取
    env_dir = task_dir / "environment"
    if env_dir.is_dir():
        for f in env_dir.iterdir():
            if f.name == "Dockerfile":
                continue
            dst = ws / f.name
            try:
                if f.is_dir():
                    shutil.copytree(f, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(f, dst)
            except Exception:
                pass

    # 路径改写: 容器路径(/root/, /app/ 等) -> 实际 workspace。
    # 用占位符两步替换, 避免替换串内含搜索串导致递归膨胀。
    import re as _re
    solve_text = solve.read_text(encoding="utf-8", errors="ignore")
    wsp = str(ws.resolve())
    PLACE = "\x00WS\x00"
    solve_text = _re.sub(r"(?<![\w.-])(/root|/app|/workspace|/output)(?=/|\"|'|\s|\)|$)",
                         PLACE, solve_text)
    solve_text = solve_text.replace(PLACE, wsp)
    # 改写版脚本放回原 oracle 目录: 保持 ${BASH_SOURCE[0]} 的 dirname 指向 oracle 目录,
    # 使"复制预置答案"型 solve.sh (cp $SCRIPT_DIR/answer.xlsx ...) 能找到答案文件
    tmp_solve = solve.parent / "solve_adapted.sh"
    tmp_solve.write_text(solve_text, encoding="utf-8")

    t0 = time.time()
    env = dict(os.environ)
    env["ORACLE"] = "1"
    try:
        r = subprocess.run(["bash", str(tmp_solve)], cwd=str(ws), capture_output=True,
                           text=True, timeout=timeout, env=env)
        result["solve_rc"] = r.returncode
        (ws.parent / "solve.log").write_text((r.stdout or "") + (r.stderr or "")[-8000:],
                                             encoding="utf-8")
    except subprocess.TimeoutExpired:
        result["solve_rc"] = -1
        result["detail"] = "solve.sh 超时"
        result["elapsed"] = time.time() - t0
        return result
    result["elapsed"] = round(time.time() - t0, 1)

    # verifier 判分
    try:
        v = run_verifier(task_dir, ws)
        result["verifier_passed"] = v.passed
        result["detail"] = v.detail[-250:]
    except Exception as e:
        result["verifier_passed"] = False
        result["detail"] = f"verifier异常: {str(e)[:150]}"
    result["oracle_ok"] = bool(result["verifier_passed"])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--only-absent", action="store_true")
    args = ap.parse_args()

    all_tasks = sorted(p.name for p in (SB / "tasks").iterdir() if p.is_dir())
    names = set(all_tasks)
    extra = sorted(p.name for p in (SB / "tasks-extra").iterdir()
                   if p.is_dir() and p.name not in names)
    todo = all_tasks + extra
    if args.tasks:
        todo = args.tasks
    elif args.only_absent:
        gt = json.load(open(OUT / "gt.json"))
        ana = json.load(open(OUT / "absent_analysis2.json"))
        # 优先校验"真实判负"(FAILED)的 49 个 + 其他 2 个: 这批标签直接依赖 verifier 正确性
        todo = ana["real_fail"] + ana["other"]
        todo = [t for t in todo if t in gt]

    out_path = OUT / "oracle_check.json"
    done = json.load(open(out_path)) if out_path.exists() else {}
    todo = [t for t in todo if t not in done or not done[t].get("oracle_ok")]
    log(f"[plan] oracle 校验 {len(todo)} 任务, workers={args.workers}")

    results = dict(done)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(check_one, t, args.timeout): t for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"task_id": t, "oracle_ok": None, "verifier_passed": None,
                     "detail": f"error: {str(e)[:150]}", "solve_rc": None, "elapsed": 0}
            results[t] = r
            log(f"[{n}/{len(todo)}] {t:<44} oracle_ok={r['oracle_ok']} ({r['elapsed']}s) {r['detail'][:60]}")
            if n % 10 == 0:
                json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=2)

    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=2)
    ok = sum(1 for r in results.values() if r.get("oracle_ok"))
    log(f"\n[done] oracle 通过 {ok}/{len(todo)} (本次) -> {out_path}")


if __name__ == "__main__":
    main()
