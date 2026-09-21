"""DS-1000 适配器: 1000 道数据科学题(numpy/pandas/scipy/matplotlib/sklearn)。

- 物料: task_text=题目 prompt; gold_trace=官方 reference_code; identifiers=库 API 名
- skill 注入: 同 library 其他题的 reference_code 作为 few-shot(不泄露本题答案)
- 金标准: 单轮 LLM 调用生成 completion -> 官方 execution.check_correctness 判分
  (已实测: 判分管线本机直接可用, 无需容器)
"""
from __future__ import annotations

import gzip
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from base import BenchmarkAdapter, ProbeMaterial, Verdict

DS1000_ROOT = Path("/data1/xiao/DS-1000")


class DS1000Adapter(BenchmarkAdapter):
    name = "ds1000"
    supports_gold = True

    def __init__(self):
        self._cache: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = {}
            with gzip.open(DS1000_ROOT / "data" / "ds1000.jsonl.gz") as f:
                for line in f:
                    x = json.loads(line)
                    self._cache[str(x["metadata"]["problem_id"])] = x
        return self._cache

    def list_tasks(self) -> list[str]:
        return sorted(self._load().keys(), key=lambda x: int(x))

    def _lib(self, x: dict) -> str:
        return x["metadata"]["library"]

    def load_material(self, task_id: str) -> ProbeMaterial:
        x = self._load()[task_id]
        gold = x["reference_code"]
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(
            task_id=f"ds1000_{task_id}",
            skill_name=self._lib(x),
            skill_md=self.load_skill_md(task_id),
            task_text=x["prompt"],
            gold_trace=gold,
            identifiers=idents,
            key_spans=spans,
        )

    def load_skill_md(self, task_id: str) -> str:
        """skill = 同 library 其他 2 题的官方参考解法(few-shot 风格示范, 不含本题答案)。"""
        x = self._load()[task_id]
        lib = self._lib(x)
        peers = [xx for tid, xx in self._load().items()
                 if self._lib(xx) == lib and tid != task_id]
        rng = random.Random(hash(task_id) % 2**32)
        picks = rng.sample(peers, min(2, len(peers)))
        parts = [f"# {lib} 数据科学技能参考\n以下为该库的典型解法风格(其他题目示例):\n"]
        for p in picks:
            parts.append(f"## 示例题\n```\n{p['reference_code'][:800]}\n```")
        return "\n".join(parts)

    # ---- 金标准: 单轮调用 + 官方判分 ----
    def run_gold(self, task_id: str, with_skill: bool,
                 llm_call=None, skill_md: str = "") -> Verdict:
        """llm_call(prompt) -> str 补全代码。由 runner 注入(指向 sglang)。"""
        x = self._load()[task_id]
        skill = skill_md if with_skill else ""
        prompt = (f"{skill}\n\n" if skill else "") + \
                 x["prompt"] + "\n\nBegin your solution below.\n"
        t0 = time.time()
        completion = self._postprocess(llm_call(prompt))
        gen_t = time.time() - t0

        test_program = (x["code_context"] + "\n"
                        + f"code = {completion!r}\n"
                        + "test_execution(code)\n")
        sys.path.insert(0, str(DS1000_ROOT))
        from execution import check_correctness
        try:
            r = check_correctness(test_program, timeout=120, completion_id=int(task_id))
            passed = bool(r["passed"])
            detail = f"gen={gen_t:.1f}s exec_passed={passed}"
        except Exception as e:
            passed, detail = False, f"判分异常: {str(e)[:150]}"
        return Verdict(passed, 1.0 if passed else 0.0, detail)

    @staticmethod
    def _postprocess(code: str) -> str:
        """官方 postprocess: 剩 markdown 围栏/标签, 取代码体。"""
        code = code.split('</code>')[0]
        code = code.replace('```python', '')
        code = code.split('```')[0]
        code = code.split('\nEND SOLUTION')[0]
        code = code.replace('<code>', '')
        return code.strip()

    def run_gold_batchable(self) -> bool:
        return True
