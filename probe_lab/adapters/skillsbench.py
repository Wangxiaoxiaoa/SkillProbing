"""skillsbench 适配器: 101 任务(主战场, 金标准已由官方 harness 容器化重建)。"""
from __future__ import annotations
from pathlib import Path
import re
from base import BenchmarkAdapter, ProbeMaterial

SB = Path("/data1/xiao/skillsbench")


class SkillsBenchAdapter(BenchmarkAdapter):
    name = "skillsbench"
    supports_gold = True   # 官方 harness: bench eval run --skill-mode with/no-skill

    def list_tasks(self):
        return sorted(p.name for p in (SB / "tasks").iterdir() if p.is_dir())

    def load_material(self, task_id: str) -> ProbeMaterial:
        td = SB / "tasks" / task_id
        skdirs = sorted((td / "environment" / "skills").glob("*/")) if (td / "environment" / "skills").is_dir() else []
        sk = skdirs[0] if skdirs else None
        skmd = (sk / "SKILL.md").read_text(errors="ignore") if sk and (sk / "SKILL.md").exists() else ""
        tm = td / "task.md"
        text = tm.read_text(errors="ignore") if tm.exists() else ""
        parts = text.split("---")
        task_text = "---".join(parts[2:]) if len(parts) >= 3 else text
        blocks = re.findall(r"```(\w*)\n(.*?)```", skmd, re.S)
        py = [b for lang, b in blocks if lang in ("python", "bash", "") and len(b.strip()) > 40]
        gold = max(py, key=len).strip() if py else ""
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(task_id, sk.name if sk else task_id, skmd,
                             task_text[:1500], gold, idents, spans)

    def load_skill_md(self, task_id: str) -> str:
        return self.load_material(task_id).skill_md
