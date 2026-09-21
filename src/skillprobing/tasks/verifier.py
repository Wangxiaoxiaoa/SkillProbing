"""任务结果判题(正确性)。

独立重构的判题逻辑:不 import skillsbench,而是运行本项目抽取的
verifier(通常是一个 pytest/unittest test_outputs.py 或对输出文件断言)。

关键适配:
  skillsbench 的 verifier 默认在沙箱内运行,硬编码 /root/xxx 路径。
  本模块在运行前把 /root/ 替换为实际 workspace 路径,并把 environment
  中的输入文件复制到 workspace,从而能在本地直接判题。

返回: 0(失败) / 1(通过)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Verdict:
    passed: bool
    score: float          # 0.0 ~ 1.0
    detail: str


def _copy_environment_inputs(task_dir: Path, workspace: Path) -> None:
    """把 task/environment 或 task/input 里的输入文件复制到 workspace(供 verifier 读取)。"""
    for src_dir in (task_dir / "environment", task_dir / "input"):
        if not src_dir.is_dir():
            continue
        for src in src_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(src_dir)
                dst = workspace / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)


def _prepare_verifier_script(verifier_dir: Path, workspace: Path) -> Path:
    """复制 verifier 脚本到临时目录,并把沙箱硬编码路径替换为实际 workspace 路径。

    skillsbench 容器内常见约定: /root/, /app/, /workspace/, /output/。
    本机运行时全部映射到实际 workspace / verifier 目录。
    """
    tmp = Path(tempfile.mkdtemp(prefix="verifier_"))
    # 把 verifier 目录下所有辅助 .py 一并拷贝, 解决 verifier import 本地模块(如 solution.py)失败
    for aux in verifier_dir.glob("*.py"):
        shutil.copy2(aux, tmp / aux.name)
    test_py = verifier_dir / "test_outputs.py"
    text = test_py.read_text(encoding="utf-8")
    ws = workspace.as_posix()
    for prefix in ("/root/", "/app/", "/workspace/", "/output/"):
        text = text.replace(f'"{prefix}', f'"{ws}/')
        text = text.replace(f"'{prefix}", f"'{ws}/")
    # 若脚本里有引用 /verifier/... 的辅助文件也映射
    text = text.replace('"/verifier/', f'"{verifier_dir.as_posix()}/')
    text = text.replace("'/verifier/", f"'{verifier_dir.as_posix()}/")
    out = tmp / "test_outputs.py"
    out.write_text(text, encoding="utf-8")
    return out


def run_verifier(task_dir: Path, workspace: Path, timeout: int = 120) -> Verdict:
    """在 workspace 上运行抽取到的 verifier 判题。

    task_dir: skill 任务目录,包含 verifier/ 和可选 environment/
    workspace: 智能体实际写结果的目录
    """
    verifier_dir = task_dir / "verifier"
    if not verifier_dir.is_dir():
        return Verdict(False, 0.0, "无 verifier 目录")

    # 把输入文件放进 workspace,让本地 verifier 能找到
    _copy_environment_inputs(task_dir, workspace)

    test_py = verifier_dir / "test_outputs.py"
    if test_py.exists():
        return _run_test(task_dir, test_py, workspace, timeout)
    sh = verifier_dir / "test.sh"
    if sh.exists():
        return _run_shell(verifier_dir, sh, workspace, timeout)
    return Verdict(False, 0.0, "verifier 无 test_outputs.py 或 test.sh")


def _run_test(task_dir: Path, test_py: Path, workspace: Path, timeout: int) -> Verdict:
    """优先 pytest; 未安装则 fallback 到 unittest。"""
    adapted = _prepare_verifier_script(task_dir / "verifier", workspace)

    # 尝试 pytest
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", str(adapted)]
    try:
        r = subprocess.run(cmd, cwd=str(workspace), capture_output=True,
                           text=True, timeout=timeout)
        combined = r.stdout + r.stderr
        # pytest 未安装时,python 能启动但会报 No module named pytest
        if r.returncode != 0 and "No module named pytest" in combined:
            pass  # fallback 到 unittest
        else:
            passed = r.returncode == 0
            return Verdict(passed, 1.0 if passed else 0.0, combined[-800:])
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return Verdict(False, 0.0, "verifier 超时")
    except Exception as e:
        return Verdict(False, 0.0, f"pytest 异常: {e}")

    # fallback: 直接执行 TestOutputs 类里的 test_ 方法(pytest 风格,类可不继承 TestCase)
    try:
        import importlib.util
        sys.path.insert(0, str(adapted.parent))
        try:
            spec = importlib.util.spec_from_file_location("test_outputs", adapted)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, "TestOutputs", None)
            if cls is None:
                return Verdict(False, 0.0, "test_outputs.py 中未找到 TestOutputs 类")
            instance = cls()
            methods = [m for m in dir(instance) if m.startswith("test_") and callable(getattr(instance, m))]
            failures = []
            for m in methods:
                try:
                    getattr(instance, m)()
                except AssertionError as e:
                    failures.append(f"{m}: {e}")
                except Exception as e:
                    failures.append(f"{m}: {type(e).__name__}: {e}")
            passed = not failures
            detail = f"tests={len(methods)} failures={len(failures)}" + (
                "\n" + "\n".join(failures) if failures else "")
            return Verdict(passed, 1.0 if passed else 0.0, detail)
        finally:
            sys.path.pop(0)
    except Exception as e:
        return Verdict(False, 0.0, f"fallback 异常: {e}")


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
