"""适配器注册表。

用法:
    from adapters import get_adapter, ADAPTERS
    adapter = get_adapter("ds1000")
    mats = adapter.load_all_materials()
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from base import BenchmarkAdapter, ProbeMaterial, Verdict
from ds1000 import DS1000Adapter
from bfcl import BFCLAdapter
from terminalbench import TerminalBenchAdapter
from taubench import TauBenchAdapter
from kernelbench import KernelBenchAdapter

ADAPTERS: dict[str, type[BenchmarkAdapter]] = {
    "ds1000": DS1000Adapter,
    "bfcl": BFCLAdapter,
    "terminalbench": TerminalBenchAdapter,
    "taubench": TauBenchAdapter,
    "kernelbench": KernelBenchAdapter,
}


def get_adapter(name: str) -> BenchmarkAdapter:
    if name not in ADAPTERS:
        raise KeyError(f"未知 benchmark: {name}, 可选: {list(ADAPTERS)}")
    return ADAPTERS[name]()
