"""SkillLearnBench 适配器: 100 已验证实例(20 域), 36 人工 SKILL.md + 官方 none 基线。"""
from __future__ import annotations
from pathlib import Path
import re
from base import BenchmarkAdapter, ProbeMaterial

SLB = Path("/data1/xiao/SkillLearnBench")


class SkillLearnBenchAdapter(BenchmarkAdapter):
    name = "skilllearnbench"
    supports_gold = True   # 官方: evaluate_skills.py --skill-path ... none

    def list_tasks(self):
        out = []
        for domain in sorted((SLB / "tasks").iterdir()):
            if domain.is_dir():
                out += [f"{domain.name}/{inst.name}" for inst in sorted(domain.iterdir()) if inst.is_dir()]
        return out

    def load_material(self, task_id: str) -> ProbeMaterial:
        td = SLB / "tasks" / task_id
        instruction = (td / "instruction.md").read_text(errors="ignore")[:2000] if (td / "instruction.md").exists() else ""
        domain = task_id.split("/")[0]
        # skill: human_authored 下该域的 SKILL.md(可能多个, 合并)
        sk_root = SLB / "skills" / "human_authored" / domain
        parts = []
        for skmd in sorted(sk_root.rglob("SKILL.md")) if sk_root.is_dir() else []:
            parts.append(skmd.read_text(errors="ignore"))
        skill_md = "\n\n---\n\n".join(parts) if parts else ""
        # gold trace: 环境/测试里的参考实现不可直接用, 取 instruction 中的代码块
        blocks = re.findall(r"```(\w*)\n(.*?)```", instruction, re.S)
        py = [b for lang, b in blocks if len(b.strip()) > 40]
        gold = max(py, key=len).strip() if py else ""
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(task_id=task_id.replace("/", "__"), skill_name=domain,
                             skill_md=skill_md, task_text=instruction,
                             gold_trace=gold, identifiers=idents, key_spans=spans)

    def load_skill_md(self, task_id: str) -> str:
        return self.load_material(task_id).skill_md
