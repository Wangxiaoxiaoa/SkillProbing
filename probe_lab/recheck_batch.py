"""批量容器化复核: 对 skillsbench 剔除任务(oracle失败38 + verifier错12)自动修复。

每任务:
  1. docker build 官方 environment/Dockerfile
  2. 容器内跑原版 oracle/solve.sh (不跳路径) + verifier → 判 verifier 是否可用
  3. 可用则把已保存的 agent workspace 产物放入容器重判 → 更新 GT(可能翻案)
  4. oracle 仍失败 → 标记 permanently_excluded
3 路并发, 断点续跑。产出: outputs/gold_standard/recheck_batch.json + 更新 gt.json
"""
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
OUT = Path(PROJ / "outputs" / "gold_standard")
_print_lock = threading.Lock()


def log(m):
    with _print_lock:
        print(m, flush=True)


def sh(cmd, timeout, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


def workdir_of(task_dir: Path) -> str:
    df = task_dir / "environment" / "Dockerfile"
    for line in df.read_text(errors="ignore").splitlines():
        if line.strip().startswith("WORKDIR"):
            return line.split()[1].rstrip("/")
    return "/root"


def recheck_one(task_name: str, timeout: int) -> dict:
    task_dir = SB / "tasks" / task_name
    if not task_dir.is_dir():
        task_dir = SB / "tasks-extra" / task_name
    img = f"sb-fix/{task_name}"
    entry = {"task_id": task_name, "build_ok": False, "oracle_pass": None,
             "with_pass": None, "without_pass": None, "conclusion": ""}

    rc, log_ = sh(["sudo", "docker", "build", "--network", "host",
                   "--build-arg", "HTTP_PROXY=http://127.0.0.1:18080",
                   "--build-arg", "HTTPS_PROXY=http://127.0.0.1:18080",
                   "-t", img, "."], timeout=2400, cwd=str(task_dir / "environment"))
    entry["build_ok"] = rc == 0
    if rc != 0:
        entry["conclusion"] = "build失败"
        entry["detail"] = log_[-200:]
        return entry
    log(f"  [{task_name}] build OK")

    wd = workdir_of(task_dir)
    verifier_dir = task_dir / "verifier"
    solve = task_dir / "oracle" / "solve.sh"
    img_tag = img

    def run_in(mode: str, ws_src: Path | None) -> tuple[bool, str]:
        vols = [f"{verifier_dir.resolve()}:/mnt/verifier:ro"]
        if solve.exists():
            vols.append(f"{solve.parent.resolve()}:/mnt/oracle:ro")
        if ws_src:
            vols.append(f"{ws_src.resolve()}:/mnt/agentws:ro")
        inner = ("pip install pytest --quiet 2>/dev/null; "
                 + (f"cd {wd} && bash /mnt/oracle/solve.sh > /tmp/solve.log 2>&1; " if mode == "oracle" else "")
                 + (f"cp -r /mnt/agentws/. {wd}/ 2>/dev/null; " if ws_src else "")
                 + f"cd {wd} && python3 -m pytest /mnt/verifier/test_outputs.py -q 2>&1 | tail -4")
        cmd = ["sudo", "docker", "run", "--rm", "--network", "host"] + sum([["-v", v] for v in vols], []) \
            + [img_tag, "bash", "-c", inner]
        rc, log_ = sh(cmd, timeout=1500)
        passed = rc == 0 and "failed" not in log_.lower() and "error" not in log_.lower()
        return passed, log_[-200:]

    if solve.exists():
        ok, log_ = run_in("oracle", None)
        entry["oracle_pass"] = ok
        log(f"  [{task_name}] oracle(容器): {'PASS' if ok else 'FAIL'}")
        if not ok:
            entry["conclusion"] = "verifier容器内也不可用, 永久剔除"
            return entry
    else:
        entry["oracle_pass"] = None   # 无 oracle: 无法验证 verifier, 保守剔除
        entry["conclusion"] = "无oracle脚本, 无法验证verifier, 剔除"
        return entry

    for cond, key in (("with_skill", "with_pass"), ("without_skill", "without_pass")):
        ws = OUT / task_name / cond / "workspace"
        if not ws.is_dir():
            entry[key] = None
            continue
        ok, _ = run_in("agent", ws)
        entry[key] = ok
        log(f"  [{task_name}] agent[{cond}](容器): {'PASS' if ok else 'FAIL'}")

    if entry["with_pass"] or entry["without_pass"]:
        entry["conclusion"] = "翻案: agent 实际做对, GT 更新为" + ("BUILTIN" if entry["without_pass"] else "NOT_BUILTIN")
    else:
        entry["conclusion"] = "维持 ABSENT (容器内也判负)"
    return entry


def main():
    ana = json.load(open(OUT / "absent_analysis2.json"))
    todo = ana["real_fail"] + ana["verifier_err"] + ana["other"]   # 50 个有 agent 产物的
    todo = sorted(set(todo))
    out_path = OUT / "recheck_batch.json"
    done = json.load(open(out_path)) if out_path.exists() else {}
    todo = [t for t in todo if t not in done]
    log(f"[plan] 批量容器复核 {len(todo)} 任务, 3 路并发")

    results = dict(done)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(recheck_one, t, 2400): t for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"task_id": t, "build_ok": False, "conclusion": f"error: {str(e)[:100]}",
                     "oracle_pass": None, "with_pass": None, "without_pass": None}
            results[t] = r
            log(f"[{n}/{len(todo)}] {t}: {r['conclusion']}")
            json.dump(results, open(out_path, "w"), indent=2, ensure_ascii=False)

    ok = sum(1 for r in results.values() if r.get("oracle_pass"))
    log(f"\n[done] verifier 可用(oracle通过) {ok}/{len(results)} -> {out_path}")


if __name__ == "__main__":
    main()
