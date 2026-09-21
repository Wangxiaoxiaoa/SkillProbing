"""KernelBench 适配器: 270 个 GPU kernel 优化任务(level1-3)。

- 物料: task_text=原始 PyTorch 模型 + 输入规格; gold_trace=官方 Model 参考实现;
        skill 注入: CUDA/Triton 优化技巧要点(合成)。
- 金标准: 生成 kernel -> 容器/GPU 上正确性对比 + 加速比。需要空闲 GPU,
  本机 GPU0/1 空闲可用(CUDA_VISIBLE_DEVICES=0)。骨架+物料层完整, 判分二期。
"""
from __future__ import annotations

from pathlib import Path

from base import BenchmarkAdapter, ProbeMaterial, Verdict

KB_ROOT = Path("/data1/xiao/KernelBench/KernelBench")
LEVELS = ["level1", "level2"]   # level3 是大模型, 耗 GPU 巨大, 默认不含

SKILL_MD = """# CUDA/PyTorch Kernel 优化技能要点

1. 用 `@triton.jit` 或手写 CUDA 替代 eager PyTorch 的逐元素/规约操作。
2. 融合相邻的逐元素算子, 避免中间显存读写(如 Conv+BN+ReLU 融合)。
3. 利用共享内存做 tile 化矩阵乘; 注意 bank conflict。
4. 规约操作用 warp-level primitives(`__shfl_down_sync`)。
5. 保持数值语义一致: 除法精度、epsilon 位置、归一化维度。
6. 输出接口必须与原 `Model.forward` 完全一致(参数与返回形状)。
"""


class KernelBenchAdapter(BenchmarkAdapter):
    name = "kernelbench"
    supports_gold = False   # GPU 判分二期(需空闲 GPU + 参考输出缓存)

    def __init__(self, levels: list[str] | None = None):
        self.levels = levels or LEVELS

    def list_tasks(self) -> list[str]:
        out = []
        for lv in self.levels:
            d = KB_ROOT / lv
            if d.is_dir():
                out += [f"{lv}/{p.name}" for p in sorted(d.glob("*.py"))]
        return out

    def load_material(self, task_id: str) -> ProbeMaterial:
        src = (KB_ROOT / task_id).read_text(errors="ignore")
        # task_text = 源码(模型定义+输入规格)
        task_text = src[:2000]
        gold = src   # 原始实现即参考轨迹(eager 版)
        idents, spans = self.extract_identifiers(gold)
        return ProbeMaterial(
            task_id=f"kb_{task_id.replace('/', '_').replace('.py', '')}",
            skill_name="cuda-optimization",
            skill_md=SKILL_MD,
            task_text=task_text,
            gold_trace=gold,
            identifiers=idents,
            key_spans=spans,
        )

    # 金标准二期:
    # 1. agent 生成 ModelNew (forward 语义一致)
    # 2. 随机输入下 outputs_allclose(reference, new) (torch.allclose rtol=1e-2)
    # 3. 计时对比(triton.testing.do_bench), skill 增益 = 加速比变化
