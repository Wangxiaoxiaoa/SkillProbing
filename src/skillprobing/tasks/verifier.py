"""任务结果判题(正确性)。

独立重构的判题逻辑:不 import skillsbench,而是运行本项目抽取的
verifier(通常是一个 pytest test_outputs.py 或对输出文件断言)。
若 verifier 缺失,则退化为"结果文本存在/格式匹配"的轻量判断。

返回: 0(失败) / 1(通过)
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Verdict:
    passed: bool
    score: float          # 0.0 ~ 1.0
    detail: str


def run_verifier(verifier_dir: Path, workspace: Path, timeout: int = 120) -> Verdict:
    """在 workspace 上运行抽取到的 verifier 判题。

    兼容两种：
      A. verifier 含 test_outputs.py -> 用 python 运行其 TestOutputs 类
      B. verifier 含 test.sh -> 直接执行脚本(reward.txt)
    独立、通用。
    """
    if not verifier_dir.is_dir():
        return Verdict(False, 0.0, "无 verifier 目录")

    test_py = verifier_dir / "test_outputs.py"
    if test_py.exists():
        return _run_pytest_test(verifier_dir, test_py, workspace, timeout)
    sh = verifier_dir / "test.sh"
    if sh.exists():
        return _run_shell(verifier_dir, sh, workspace, timeout)
    return Verdict(False, 0.0, "verifier 无 test_outputs.py 或 test.sh")


def _run_pytest_test(verifier_dir: Path, test_py: Path, workspace: Path, timeout: int) -> Verdict:
    """用 pytest 运行 test_outputs.py(它会在 workspace 里检查输出文件)。"""
    cmd = ["python3", "-m", "pytest", "-q", "--no-header", str(test_py)]
    try:
        r = subprocess.run(cmd, cwd=str(workspace), capture_output=True,
                           text=True, timeout=timeout)
        # pytest 退出码 0 = 全部通过
        passed = r.returncode == 0
        tail = (r.stdout + r.stderr)[-500:]
        return Verdict(passed, 1.0 if passed else 0.0, tail)
    except subprocess.TimeoutExpired:
        return Verdict(False, 0.0, "verifier 超时")
    except FileNotFoundError:
        return Verdict(False, 0.0, "pytest 未安装")
    except Exception as e:
        return Verdict(False, 0.0, f"verifier 异常: {e}")


def _run_shell(verifier_dir: Path, sh: Path, workspace: Path, timeout: int) -> Verdict:
    """执行 test.sh: 它会在 workspace 运行并写 reward.txt。"""
    try:
        r = subprocess.run(["bash", str(sh)], cwd=str(workspace),
                           capture_output=True, text=True, timeout=timeout)
        score = 0.0
        for cand in (workspace / "logs" / "verifier" / "reward.txt",
                     workspace / "reward.txt",
                     Path("/") / "logs" / "verifier" / "reward.txt"):
            if cand.exists():
                try:
                    score = float(cand.read_text().strip())
                except Exception:
                    score = 0.0
                break
        return Verdict(score >= 1.0, score, f"exit={r.returncode} reward={score}")
    except subprocess.TimeoutExpired:
        return Verdict(False, 0.0, "test.sh 超时")
    except Exception as e:
        return Verdict(False, 0.0, f"test.sh 异常: {e}")


def parse_result_score(results_dir: Path) -> float:
    """从 outputs 读已保存的判题分(0~1)。"""
    rp = results_dir / "reward.txt"
    if rp.exists():
        return float(rp.read_text().strip())
    return 0.0