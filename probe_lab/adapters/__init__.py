"""适配器注册表。用法: get_adapter('skillsbench').load_all_materials()"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from base import BenchmarkAdapter, ProbeMaterial, Verdict
from skillsbench import SkillsBenchAdapter
from skilllearnbench import SkillLearnBenchAdapter

ADAPTERS = {"skillsbench": SkillsBenchAdapter, "skilllearnbench": SkillLearnBenchAdapter}

def get_adapter(name: str) -> BenchmarkAdapter:
    if name not in ADAPTERS:
        raise KeyError(f"未知 benchmark: {name}, 可选: {list(ADAPTERS)}")
    return ADAPTERS[name]()
