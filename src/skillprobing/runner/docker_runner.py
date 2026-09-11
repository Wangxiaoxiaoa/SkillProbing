"""在 Docker 容器内运行 task,与 skillsbench 对齐。

每个 task:
  1. 基于 task/environment/Dockerfile 构建带 opencode 的运行镜像
  2. 启动容器,挂载 skills / workspace / verifier / opencode config
  3. 容器内执行 opencode,然后执行 verifier
  4. 结果保留在宿主机 output_dir

Agent 的 LLM 调用通过环境变量/配置指向宿主机网关,从而截获 logprobs 到宿主机。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skillprobing.run.agent import build_prompt
from skillprobing.skills.skill_loader import TaskSpec, load_task_dir
from skillprobing.tasks.verifier import Verdict


@dataclass
class DockerRunResult:
    returncode: int
    stdout: str
    stderr: str
    workspace: Path
    verifier: Verdict | None


def build_task_image(task_dir: Path, tag: str | None = None) -> str:
    """构建最小运行镜像(避免容器内 apt-get,依赖挂载宿主二进制/库)。"""
    env_dir = task_dir / "environment"
    dockerfile = env_dir / "Dockerfile"
    if not dockerfile.exists():
        raise FileNotFoundError(f"{task_dir} 缺少 environment/Dockerfile")

    tag = tag or f"skillprobing/{task_dir.name}:latest"
    # 生成最小 Dockerfile: 只 FROM + WORKDIR,不执行任何 RUN
    # 输入文件、skills、verifier、python/opencode 全部运行时挂载
    mini = env_dir / "Dockerfile.minimal"
    mini.write_text("FROM ubuntu:24.04\nWORKDIR /root\n", encoding="utf-8")
    try:
        subprocess.run(
            ["docker", "build", "-f", "Dockerfile.minimal", "-t", tag, "."],
            check=True,
            cwd=str(env_dir),
        )
    finally:
        mini.unlink(missing_ok=True)
    return tag


def _opencode_config(gateway_url: str) -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "gateway": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "SkillProbing Gateway",
                "options": {
                    "baseURL": gateway_url.rstrip("/"),
                    "apiKey": "sk-test",
                },
                "models": {
                    "model": {
                        "name": "Gateway Model",
                        "limit": {"context": 1048576, "output": 8192},
                    }
                },
            }
        },
        "model": "gateway/model",
    }


def run_task_in_docker(
    task_dir: Path,
    prompt: str,
    output_dir: Path,
    gateway_url: str = "http://127.0.0.1:5909/v1",
    timeout: int = 600,
) -> DockerRunResult:
    """在容器内完整运行一个 task(含 verifier),结果落盘到 output_dir。"""
    output_dir = Path(output_dir).resolve()
    task_dir = Path(task_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = output_dir / "workspace"
    workspace.mkdir(exist_ok=True)

    image = build_task_image(task_dir)

    task_file = output_dir / "task.txt"
    task_file.write_text(prompt, encoding="utf-8")
    config_file = output_dir / "opencode_config.json"
    config_file.write_text(json.dumps(_opencode_config(gateway_url)), encoding="utf-8")
    entrypoint = output_dir / "entrypoint.sh"
    entrypoint.write_text(
        '#!/bin/bash\nset -e\nexport NODE_TLS_REJECT_UNAUTHORIZED=0\ncd /root/workspace\n/home/uos/.local/lib/node_modules/opencode-ai/bin/opencode.exe run --model gateway/model --auto < /root/task.txt\npython3 /verifier/test_outputs.py\n',
        encoding="utf-8",
    )

    skills_dir = task_dir / "environment" / "skills"
    verifier_dir = task_dir / "verifier"
    env_dir = task_dir / "environment"
    volumes = [
        f"{task_file.resolve()}:/root/task.txt:ro",
        f"{config_file.resolve()}:/root/.config/opencode/config.json:ro",
        f"{entrypoint.resolve()}:/root/entrypoint.sh:ro",
        f"{workspace.resolve()}:/root/workspace",
        # 挂载宿主运行环境(容器内 apt 不可用时使用)
        f"/usr:/usr:ro",
        f"/lib:/lib:ro",
        f"/lib64:/lib64:ro",
        f"/home/uos/.local/lib/node_modules/opencode-ai:/home/uos/.local/lib/node_modules/opencode-ai:ro",
    ]
    # 把 environment 下的输入文件(非 skills/Dockerfile)挂载到 /root
    if env_dir.is_dir():
        for f in env_dir.iterdir():
            if f.name in ("skills", "Dockerfile") or f.is_dir():
                continue
            volumes.append(f"{f.resolve()}:/root/{f.name}:ro")
    if skills_dir.is_dir():
        volumes.append(f"{skills_dir.resolve()}:/root/.claude/skills:ro")
    if verifier_dir.is_dir():
        volumes.append(f"{verifier_dir.resolve()}:/verifier:ro")

    cmd = ["docker", "run", "--rm", "--network", "host"]
    for v in volumes:
        cmd += ["-v", v]
    cmd += [image, "bash", "/root/entrypoint.sh"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        (output_dir / "container.stdout").write_text(r.stdout, encoding="utf-8")
        (output_dir / "container.stderr").write_text(r.stderr, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        (output_dir / "container.stdout").write_text(stdout, encoding="utf-8")
        (output_dir / "container.stderr").write_text(stderr, encoding="utf-8")
        return DockerRunResult(-1, stdout, stderr, workspace, None)

    # 解析 verifier 结果(从容器 stdout 里找 pytest/unittest 输出)
    verifier = None
    if verifier_dir.is_dir():
        verifier = _parse_verifier_output(r.stdout + r.stderr, workspace)

    return DockerRunResult(r.returncode, r.stdout, r.stderr, workspace, verifier)


def _parse_verifier_output(combined: str, workspace: Path) -> Verdict:
    """简单从输出中推断 verifier 是否通过。"""
    passed = "failures=0" in combined and "errors=0" in combined
    if not passed:
        passed = "passed" in combined.lower() and "failed" not in combined.lower()
    # 更准确的: 直接在宿主机 rerun verifier
    from skillprobing.tasks.verifier import run_verifier
    v = run_verifier(task_dir=workspace.parent, workspace=workspace)
    return v


def run_with_and_without_skill(
    task_dir: Path,
    output_root: Path,
    gateway_url: str = "http://127.0.0.1:5909/v1",
    timeout: int = 600,
) -> tuple[DockerRunResult, DockerRunResult]:
    """跑有 skill 和无 skill 两组,用于后续熵判断。"""
    task = load_task_dir(task_dir)
    prompt_with = build_prompt(task.prompt, task.skills_dir)
    prompt_without = build_prompt(task.prompt, None)

    r_with = run_task_in_docker(
        task_dir, prompt_with, output_root / "with_skill", gateway_url, timeout
    )
    r_without = run_task_in_docker(
        task_dir, prompt_without, output_root / "without_skill", gateway_url, timeout
    )
    return r_with, r_without
