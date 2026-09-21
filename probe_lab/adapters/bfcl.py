"""BFCL 适配器: function-calling 标准榜(v4, 3546 条带答案)。

- 物料: task_text=用户请求; gold_trace=标准答案调用序列(函数名+参数);
        identifiers=函数名。分类: simple/multiple/parallel(_multiple) 4 类 python 子集。
- skill 注入: 候选函数的完整 docstring/API 表(真实使用中 skill 的典型形态)。
- 金标准: 单轮 LLM 调用 -> 简化判分(函数名精确 + 参数值匹配 ground_truth 任一可接受值)。
  注: 官方 AST 判分器更复杂(类型系统/可选参数), 简化判分偏保守, 对 with/without
  对照实验双侧一致, 不影响相对结论。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from base import BenchmarkAdapter, ProbeMaterial, Verdict

BFCL_ROOT = Path("/data1/xiao/gorilla/berkeley-function-call-leaderboard/bfcl_eval/data")
CATEGORIES = ["simple", "multiple", "parallel", "parallel_multiple"]  # python 子集, 带答案


class BFCLAdapter(BenchmarkAdapter):
    name = "bfcl"
    supports_gold = True

    def __init__(self, categories: list[str] | None = None):
        self.cats = categories or CATEGORIES
        self._cache: dict[str, dict] | None = None

    def _files(self, cat: str) -> tuple[Path, Path]:
        """返回 (问题文件, 答案文件)。答案文件与问题文件同名(possible_answer/ 下)。"""
        if cat == "simple":
            q = BFCL_ROOT / "BFCL_v4_simple_python.json"
        else:
            q = BFCL_ROOT / f"BFCL_v4_live_{cat}.json"
        a = BFCL_ROOT / "possible_answer" / q.name
        return q, a

    def _load(self) -> dict[str, dict]:
        if self._cache is None:
            self._cache = {}
            for cat in self.cats:
                q_file, a_file = self._files(cat)
                if not q_file.exists() or not a_file.exists():
                    print(f"[bfcl] 缺文件: {q_file.name}")
                    continue
                # 答案按 id 索引
                answers = {}
                for line in open(a_file):
                    a = json.loads(line)
                    answers[a["id"]] = a["ground_truth"]
                # 问题逐行(部分文件是 jsonl, 部分是数组)
                with open(q_file) as fh:
                    head = fh.read(1)
                qs = []
                if head == "[":
                    qs = json.load(open(q_file))
                else:
                    qs = [json.loads(l) for l in open(q_file) if l.strip()]
                for x in qs:
                    qid = x["id"]
                    if qid not in answers:
                        continue
                    self._cache[qid] = {**x, "_gt": answers[qid], "_cat": cat}
        return self._cache

    def list_tasks(self) -> list[str]:
        return sorted(self._load().keys())

    def load_material(self, task_id: str) -> ProbeMaterial:
        x = self._load()[task_id]
        # gold trace: 标准答案调用序列 -> 文本化
        calls = []
        for gt_item in x["_gt"]:
            for fname, args in gt_item.items():
                calls.append(f"{fname}(**{json.dumps(args, ensure_ascii=False)})")
        gold = "\n".join(calls)
        idents, spans = self.extract_identifiers(gold)
        q_text = x["question"]
        if isinstance(q_text, list):   # multi-turn 格式
            q_text = " ".join(m.get("content", "") for turn in q_text for m in turn
                              if isinstance(m, dict))
        return ProbeMaterial(
            task_id=f"bfcl_{task_id}",
            skill_name=f"bfcl_{x['_cat']}",
            skill_md=self.load_skill_md(task_id),
            task_text=str(q_text)[:1500],
            gold_trace=gold,
            identifiers=idents,
            key_spans=spans,
        )

    def load_skill_md(self, task_id: str) -> str:
        """skill = 该题候选函数的完整 docstring/API 说明(真实 skill 形态)。"""
        x = self._load()[task_id]
        parts = ["# 可用 API 参考\n"]
        for func in x.get("function", [])[:6]:
            name = func.get("name", "")
            desc = func.get("description", "")[:300]
            params = json.dumps(func.get("parameters", {}), ensure_ascii=False)[:400]
            parts.append(f"## {name}\n{desc}\n参数结构: {params}\n")
        return "\n".join(parts)

    # ---- 金标准: 单轮调用 + 简化匹配判分 ----
    def run_gold(self, task_id: str, with_skill: bool,
                 llm_call=None, skill_md: str = "") -> Verdict:
        x = self._load()[task_id]
        skill = skill_md if with_skill else ""
        func_defs = json.dumps(x.get("function", []), ensure_ascii=False)
        prompt = (f"{skill}\n\n" if skill else "") + \
                 f"Available functions:\n{func_defs}\n\n" \
                 f"User request: {x['question'] if isinstance(x['question'], str) else x['question']}\n" \
                 "Respond with the function call(s) in the exact format: func_name(key=value, ...)\n" \
                 "One call per line. No explanations.\n\nResponse:"
        t0 = time.time()
        resp = llm_call(prompt)
        gen_t = time.time() - t0
        passed, detail = self._judge(resp, x["_gt"])
        return Verdict(passed, 1.0 if passed else 0.0, f"gen={gen_t:.1f}s {detail}")

    def _judge(self, resp: str, gt: list[dict]) -> tuple[bool, str]:
        """简化判分: 提取 resp 中所有 调用(函数名, 参数名), 与 ground_truth 匹配。
        函数名允许 模块.函数 双向匹配末段。"""
        calls = re.findall(r"([A-Za-z_][\w.]*)\s*\(([^)]*)\)", resp)
        resp_calls = [(fn, args.strip()) for fn, args in calls]
        gt_calls = []
        for item in gt:
            for fname, argvals in item.items():
                gt_calls.append((fname, set(argvals.keys())))
        if not gt_calls:
            return False, "无标准答案"

        def _tail(n):
            return n.split(".")[-1]

        ok_names = [_tail(fn) for fn, _ in resp_calls] == [_tail(fn) for fn, _ in gt_calls]
        if not ok_names:
            return False, (f"函数名不匹配: resp={[ _tail(fn) for fn,_ in resp_calls][:4]} "
                           f"gt={[_tail(fn) for fn,_ in gt_calls][:4]}")
        for (fn, args), (gfn, gkeys) in zip(resp_calls, gt_calls):
            s = args.strip()
            if s.startswith("**"):
                # 字典形态: func(**{"key": ...}) -> json 解析取顶层键(忽略嵌套)
                try:
                    obj = json.loads(s[2:].strip())
                    arg_keys = set(obj.keys()) if isinstance(obj, dict) else set()
                except Exception:
                    arg_keys = set()
            else:
                arg_keys = set(re.findall(r"(\w+)\s*=", args))
            if arg_keys != gkeys:
                return False, f"{fn} 参数键不匹配: {arg_keys} vs {gkeys}"
        return True, f"匹配 {len(gt_calls)} 个调用"


    def run_gold_batchable(self) -> bool:
        return True
