"""按任务编排的金标准管线: 一个容器生命周期内完成全部验证。

每任务流程:
  1. docker build(1次)
  2. 容器常驻 → oracle 跑标准解法 + 判分 → 不合格则跳过 GLM, 直接下一个任务
  3. 合格 → 重置工作区 → GLM with-skill 执行 → 判分
  4. 重置工作区 → GLM without-skill 执行 → 判分
  5. 收结果, 销毁容器, 下一个任务
3 路并发, 断点续跑。输出: outputs/task_pipeline/<task>.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROJ = Path("/data1/xiao/SkillProbing")
SB = Path("/data1/xiao/skillsbench")
RUNS = PROJ / "outputs" / "task_pipeline"
OPENCODE = "/home/uos/node-v22.23.0-linux-x64/bin/opencode"
_lock = threading.Lock()


def log(m):
    with _lock:
        print(m, flush=True)


def sh(cmd: str, timeout: int, **kw) -> tuple[int, str]:
    try:
        r = subprocess.run(["sg", "docker", "-c", cmd], capture_output=True, text=True,
                           timeout=timeout, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT({timeout}s)"


def process_task(task: str, model: str, agent_timeout: int) -> dict:
    td = SB / "tasks" / task
    img = f"tpipe/{task}"
    entry = {"task": task, "oracle": None, "with_skill": None, "without_skill": None,
             "final": ""}

    # ---- 1. build ----
    rc, log_ = sh(f"docker build --network host "
                  f"--build-arg HTTP_PROXY=http://172.17.0.1:18080 "
                  f"--build-arg HTTPS_PROXY=http://172.17.0.1:18080 "
                  f"--build-arg http_proxy=http://172.17.0.1:18080 "
                  f"--build-arg https_proxy=http://172.17.0.1:18080 "
                  f"-t {img} .", 3600, cwd=str(td / "environment"))
    if rc != 0:
        entry["final"] = "build失败"
        entry["detail"] = log_[-200:]
        log(f"[{task}] build ❌")
        return entry
    log(f"[{task}] build ✅")

    # ---- 2. 起常驻容器 ----
    cname = f"tpipe-{task}"
    sh(f"docker rm -f {cname} 2>/dev/null; true", 30)
    rc, log_ = sh(f"docker run -d --name {cname} --network host "
                  f"-e OPENAI_API_KEY=Tongxin123 "
                  f"-e OPENAI_BASE_URL=http://127.0.0.1:5919/v1 "
                  f"{img} sleep infinity", 120)
    if rc != 0:
        entry["final"] = "容器启动失败"
        entry["detail"] = log_[-200:]
        return entry

    try:
        # 容器内装判分依赖(pytest)
        sh(f"docker exec {cname} bash -c "
           f"'pip3 install --break-system-packages pytest pytest-json-ctrf 2>/dev/null "
           f"|| pip install --break-system-packages pytest pytest-json-ctrf'", 600)

        # ---- 3. oracle 校准 ----
        if (td / "oracle" / "solve.sh").exists():
            sh(f"docker exec {cname} mkdir -p /logs/verifier /logs/agent", 30)
            sh(f"docker cp {td/'oracle'} {cname}:/oracle", 60)
            rc, log_ = sh(f"docker exec {cname} bash -c "
                          f"'/oracle/solve.sh > /logs/agent/oracle.txt 2>&1; "
                          f"bash /verifier/test.sh > /logs/verifier/stdout.txt 2>&1; "
                          f"cat /logs/verifier/reward.txt 2>/dev/null'", agent_timeout)
            oracle_pass = log_.strip() == "1"
            entry["oracle"] = {"passed": oracle_pass, "rc": rc, "detail": log_[-150:]}
            log(f"[{task}] oracle: {'✅' if oracle_pass else '❌'}")
            if not oracle_pass:
                entry["final"] = "oracle 不合格(考场问题), 跳过 GLM"
                return entry
        else:
            entry["oracle"] = {"passed": None, "detail": "无oracle脚本(视为合格)"}
            log(f"[{task}] 无 oracle, 视为合格")

        # ---- 4/5. GLM with/without skill ----
        # 挂载 opencode + skill 注入由 GLM prompt 承载(skills 文件已拷入)
        sh(f"docker exec {cname} mkdir -p /logs/agent /root/.local/bin", 30)
        sh(f"docker cp {OPENCODE} {cname}:/usr/local/bin/opencode", 60)
        sh(f"docker exec {cname} chmod +x /usr/local/bin/opencode", 30)

        for cond, skills in (("with_skill", True), ("without_skill", False)):
            # 重置工作区(清上一轮产物)
            sh(f"docker exec {cname} bash -c "
               f"'cd $(docker exec {cname} bash -c \"cd /root 2>/dev/null && pwd\" || echo /app) "
               f"&& ls | grep -vE \"^(offer|employee|sensor|data|input|output_schema|skills|uv|uvx|environment)\" "
               f"| xargs -r rm -rf' 2>/dev/null; true", 30)
            # 构造 prompt(instruction + 可选 skill)
            inst = (td / "instruction.md")
            prompt_src = inst if inst.exists() else (td / "task.md")
            prompt = prompt_src.read_text(errors="ignore")[:6000] if prompt_src.exists() else ""
            if skills:
                skdir = td / "environment" / "skills"
                for skmd in sorted(skdir.rglob("SKILL.md")) if skdir.is_dir() else []:
                    prompt += ("\n\n[可用技能]\n" + skmd.read_text(errors="ignore")[:3000])
            pfile = Path(f"/tmp/tpipe_prompt_{task}_{cond}.txt")
            pfile.write_text(prompt, encoding="utf-8")
            sh(f"docker cp {pfile} {cname}:/tmp/task_prompt.txt", 30)

            rc, out = sh(f"docker exec {cname} bash -c "
                         f"'opencode run --model glm53flash -p \"$(cat /tmp/task_prompt.txt)\" "
                         f"> /logs/agent/{cond}.txt 2>&1; echo done'", agent_timeout)
            # 判分
            rc, log_ = sh(f"docker exec {cname} bash -c "
                          f"'bash /verifier/test.sh > /logs/verifier/stdout.txt 2>&1; "
                          f"cat /logs/verifier/reward.txt 2>/dev/null'", 600)
            passed = log_.strip() == "1"
            entry[cond] = {"passed": passed, "detail": log_[-120:]}
            log(f"[{task}] {cond}: {'✅ PASS' if passed else '❌ fail'}")

        # ---- 6. GT 判定 ----
        w = entry["with_skill"]["passed"]
        wo = entry["without_skill"]["passed"]
        entry["final"] = ("BUILTIN" if wo else ("NOT_BUILTIN" if w else "ABSENT"))
        log(f"[{task}] GT: {entry['final']}")
    finally:
        sh(f"docker rm -f {cname} 2>/dev/null; true", 30)
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--agent-timeout", type=int, default=900)
    args = ap.parse_args()

    all_tasks = sorted(p.name for p in (SB / "tasks").iterdir() if p.is_dir())
    tasks = args.tasks or all_tasks

    out_path = RUNS / "results.json"
    RUNS.mkdir(parents=True, exist_ok=True)
    results = json.load(open(out_path)) if out_path.exists() else {}
    todo = [t for t in tasks if t not in results]
    log(f"[plan] {len(todo)} 任务, workers={args.workers}")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(process_task, t, "glm53flash", args.agent_timeout): t
                for t in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                r = {"task": t, "final": f"error: {str(e)[:120]}"}
            results[t] = r
            json.dump(results, open(out_path, "w"), indent=1, ensure_ascii=False)

    from collections import Counter
    c = Counter(r.get("final") for r in results.values())
    log(f"\n[done] {len(results)} 任务: {dict(c)}")


if __name__ == "__main__":
    main()
