"""Benchmark 适配器抽象基类。

统一接口, 使五种探测方案(S1-S5)与金标准流程可跨 benchmark 复用:
  - 探测物料: list_tasks() + load_material()  -> ProbeMaterial(供 S1-S5)
  - skill 注入: load_skill_md()               -> with_skill 条件的注入内容
  - 金标准: supports_gold + run_gold()        -> Verdict(可选实现)

skill 注入策略(各 benchmark 不同, 见各适配器 docstring):
  原生 skill(skillsbench SKILL.md / tau-bench wiki.md) 直接用;
  无原生 skill 的 benchmark 合成(同库参考解法 few-shot / API 文档 / 操作要点)。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProbeMaterial:
    """一个任务的探测物料(与 S1-S5 方案解耦)。"""
    task_id: str
    skill_name: str
    skill_md: str                 # with_skill 条件的注入内容
    task_text: str                # 任务描述(探测 prompt 主体)
    gold_trace: str               # 标准执行轨迹(代码/工具调用/命令)
    identifiers: list[str] = field(default_factory=list)
    key_spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Verdict:
    passed: bool
    score: float
    detail: str = ""


class BenchmarkAdapter(ABC):
    name: str = "base"
    supports_gold: bool = False   # 是否实现了 run_gold

    # ---- 探测物料(必须实现) ----
    @abstractmethod
    def list_tasks(self) -> list[str]:
        """枚举全部 task_id。"""

    @abstractmethod
    def load_material(self, task_id: str) -> ProbeMaterial:
        """加载单任务探测物料。"""

    def load_all_materials(self, limit: int | None = None) -> list[ProbeMaterial]:
        mats = []
        for t in self.list_tasks():
            try:
                mats.append(self.load_material(t))
            except Exception as e:
                print(f"[{self.name}] 物料加载失败 {t}: {str(e)[:100]}")
            if limit and len(mats) >= limit:
                break
        return mats

    # ---- 探测物料共性: 标识符提取(与 materials.py 逻辑一致) ----
    _IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\b")
    _GENERIC = {"import", "from", "sys", "path", "append", "print", "self", "this",
                "return", "with", "open", "True", "False", "None", "str", "int",
                "float", "list", "dict", "len", "for", "in", "if", "else", "def",
                "class", "text", "file", "data", "value", "result", "report", "args"}

    def extract_identifiers(self, trace: str) -> tuple[list[str], list[tuple[int, int]]]:
        idents, spans = [], []
        for line_match in re.finditer(r"[^\n]*(?:\n|$)", trace):
            line = line_match.group(0)
            off = line_match.start()
            if line.lstrip().startswith("#"):
                continue
            for m in self._IDENT_RE.finditer(line):
                w = m.group(1)
                if not self._is_real_identifier(w):
                    continue
                idents.append(w)
                spans.append((off + m.start(), off + m.end()))
        return idents, spans

    @staticmethod
    def _is_real_identifier(w: str) -> bool:
        if w in BenchmarkAdapter._GENERIC:
            return False
        if "_" in w:
            return len(w) >= 6 and not w.isupper()
        if w[0].isupper():
            return bool(re.search(r"[a-z][A-Z]", w))
        return False

    # ---- 金标准(可选实现) ----
    def run_gold(self, task_id: str, with_skill: bool,
                 workspace: Path | None = None) -> Verdict:
        raise NotImplementedError(f"{self.name} 未实现金标准执行")

    def run_gold_batchable(self) -> bool:
        """金标准是否可全自动批量执行(不需要人工/重型环境)。"""
        return False
