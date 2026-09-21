"""容器化复核: 对 FP 争议任务, 用任务官方 Dockerfile 构建环境, 在容器内
1) 跑原版 oracle/solve.sh + verifier (sanity: 仪器正常)
2) 把已保存的 agent workspace 产物放入标准位置, 容器内 verifier 重判
对比裸机判定, 确认 GT 标签是否被裸机 verifier 误杀。

用法: python3 container_recheck.py [--tasks a b ...]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
SB = Path("/data1/xiao/skillsbench")
OUT = PROJ / "outputs" / "gold_standard"


def sh(cmd: list[str], timeout: int = 1200, **kw) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


def container_workdir(task_dir: Path) -> str:
    """从 Dockerfile 提取 WORKDIR(默认 /root)。"""
    df = task_dir / "environment" / "Dockerfile"
    for line in df.read_text(errors="ignore").splitlines():
        if line.strip().startswith("WORKDIR"):
            return line.split()[1].rstrip("/")
    return "/root"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=None)
    args = ap.parse_args()

    tasks = args.tasks or json.load(open(PROJ / "outputs/probe_lab/controversial_tasks.json"))["fp"]
    out_path = PROJ / "outputs/probe_lab/container_recheck.json"
    results = json.load(open(out_path)) if out_path.exists() else {}

    for t in tasks:
        if t in results:
            print(f"[skip] {t} 已有结果")
            continue
        task_dir = SB / "tasks" / t
        if not task_dir.is_dir():
            task_dir = SB / "tasks-extra" / t
        print(f"\n===== {t} =====", flush=True)
        entry = {"task_id": t, "build_ok": False, "oracle_pass": None,
                 "with_pass": None, "without_pass": None, "detail": {}}

        # 1) build
        t0 = time.time()
        rc, log = sh(["sudo", "docker", "build",
                      "--network", "host",
                      "--build-arg", "HTTP_PROXY=http://127.0.0.1:18080",
                      "--build-arg", "HTTPS_PROXY=http://127.0.0.1:18080",
                      "--build-arg", "http_proxy=http://127.0.0.1:18080",
                      "--build-arg", "https_proxy=http://127.0.0.1:18080",
                      "-t", f"sb-recheck/{t}", "."],
                     timeout=2400, cwd=str(task_dir / "environment"))
        entry["build_ok"] = rc == 0
        entry["detail"]["build"] = log[-300:] if rc != 0 else f"ok ({time.time()-t0:.0f}s)"
        print(f"  build: {'OK' if rc==0 else 'FAIL'} ({time.time()-t0:.0f}s)")
        if rc != 0:
            print("  ", log[-200:].replace("\n", " "))
            results[t] = entry
            json.dump(results, open(out_path, "w"), indent=2, ensure_ascii=False)
            continue

        wd = container_workdir(task_dir)
        verifier_dir = task_dir / "verifier"
        solve = task_dir / "oracle" / "solve.sh"
        img = f"sb-recheck/{t}"

        def run_in_container(mode: str, ws_src: Path | None) -> tuple[bool, str]:
            """容器内: 拷产物到 WORKDIR -> (oracle 模式则先跑 solve.sh) -> pytest verifier"""
            vol_ws = f"{ws_src.resolve()}:/mnt/agentws:ro" if ws_src else ""
            vol_v = f"{verifier_dir.resolve()}:/mnt/verifier:ro"
            vol_o = f"{solve.parent.resolve()}:/mnt/oracle:ro"
            inner = (
                f"cp -r /mnt/agentws/. {wd}/ 2>/dev/null; "
                f"pip install pytest --quiet 2>/dev/null || pip3 install pytest --quiet 2>/dev/null; "
                + (f"cd {wd} && bash /mnt/oracle/solve.sh > /tmp/solve.log 2>&1; " if mode == "oracle" else "")
                + f"cd {wd} && python3 -m pytest /mnt/verifier/test_outputs.py -q 2>&1 | tail -5"
            )
            cmd = ["sudo", "docker", "run", "--rm", "--network", "host",
                   "-v", vol_v, "-v", vol_o]
            if vol_ws:
                cmd += ["-v", vol_ws]
            cmd += [img, "bash", "-c", inner]
            rc, log = sh(cmd, timeout=1500)
            passed = rc == 0 and ("passed" in log or "no tests ran" not in log)
            # pytest 输出解析: "N passed" 且无 failed/error
            if "failed" in log or "error" in log.lower():
                passed = False
            return passed, log[-300:]

        # 2) oracle sanity
        ok, log = run_in_container("oracle", None)
        entry["oracle_pass"] = ok
        entry["detail"]["oracle"] = log[-200:]
        print(f"  oracle(容器): {'PASS ✅' if ok else 'FAIL'}")

        # 3) agent workspace 重判
        for cond, key in (("with_skill", "with_pass"), ("without_skill", "without_pass")):
            ws = OUT / t / cond / "workspace"
            if not ws.is_dir():
                entry[key] = None
                continue
            ok, log = run_in_container("agent", ws)
            entry[key] = ok
            entry["detail"][cond] = log[-200:]
            print(f"  agent[{cond}](容器): {'PASS' if ok else 'FAIL'}")

        # 结论
        if entry["oracle_pass"]:
            if entry["with_pass"] or entry["without_pass"]:
                entry["conclusion"] = "翻案: agent 实际做对了, 裸机 verifier 误杀"
            else:
                entry["conclusion"] = "维持: 容器内 verifier 也判负, agent 真没做对"
        else:
            entry["conclusion"] = "verifier 在容器内也不可用, 任务保持剔除"
        print(f"  => {entry['conclusion']}")
        results[t] = entry
        json.dump(results, open(out_path, "w"), indent=2, ensure_ascii=False)

    print(f"\n[done] -> {out_path}")


if __name__ == "__main__":
    main()
