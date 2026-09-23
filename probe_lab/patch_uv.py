"""给 skillsbench 全部任务的 environment/Dockerfile 幂等追加 uv 预装层。

原因: verifier/test.sh 运行时自装 uv(curl github 下载), 在受限网络下不可靠;
build 阶段有代理 build-args, 预装可靠。仅追加依赖, 不改任务语义。
"""
from __future__ import annotations

import sys
from pathlib import Path

SB = Path("/data1/xiao/skillsbench")

UV_BLOCK = """
# [env-adapt] preinstall uv via PyPI (GitHub unreachable in this network;
# PyPI is reachable. uv+uvx land in /usr/local/bin, visible to all users)
RUN pip3 install --break-system-packages uv \
 || pip install --break-system-packages uv
"""


def main():
    patched, skipped = [], []
    for task_dir in sorted((SB / "tasks").iterdir()):
        if not task_dir.is_dir():
            continue
        df = task_dir / "environment" / "Dockerfile"
        if not df.exists():
            skipped.append(task_dir.name)
            continue
        text = df.read_text(errors="ignore")
        if "[env-adapt]" in text:
            skipped.append(task_dir.name)
            continue
        df.write_text(text.rstrip() + "\n" + UV_BLOCK)
        patched.append(task_dir.name)
    print(f"patched {len(patched)}, skipped {len(skipped)}")


if __name__ == "__main__":
    main()
