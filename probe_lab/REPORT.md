# Probe Lab 实验报告: 零环境 skill 内化探测方法（最终版）

**目标**: 不搭建真实执行环境, 仅用模型输出 token 概率(prefill NLL / 生成熵 / top-k 分布)泛化判断 skill 是否已内化进模型权重。
**目标模型**: GLM-5.3-Flash (`glm53flash@5919`)
**金标准**: skillsbench (benchflow-ai) 101 任务, agent(opencode+GLM) 带/不带 skill 真实执行 + verifier 判题。
**验证规模**: 101 任务金标准(经清洗+容器复核) → 有效仲裁集 31 任务; 五种探测方案 × 31 种单/融合组合。

## 探测方案

| 方案 | 核心思路 | 单方案 F1 |
|------|---------|----------|
| S1 生成熵 | 任务名盲测, 生成计划 token 平均熵 | 0.52 (无判别力, 淘汰) |
| S2 NLL 反事实对照 | SKILL.md 标准用法代码 vs 同构反事实(标识符跨 skill 替换), prefill NLL 差 | 0.50 |
| S3 上下文依赖 | 同一 trace 有/无 skill 全文提示的 NLL 差, 差小=内化 | 0.41 |
| S4 工具名盲测 | "该任务最合适的专用工具" top-k 中本 skill 工具名 logprob 优势 | 0.59 |
| S5 交叉匹配 | 任务×轨迹 N×N NLL 矩阵, 双中心化后对角线检验 | 0.23 (组级信号显著 t=9.11) |

## 金标准质量控制(关键工程结论)

裸机直跑 skillsbench verifier 不可靠(verifier 设计于容器沙箱内):
- 首轮 101 任务: ABSENT 83 个, 其中 **环境失败占 39%**(agent 超时 20 + verifier 依赖缺失 12 + 裸机误杀)
- 修复: 路径映射(/root/,/app/ 等容器约定) + 补装 10 个领域包(qutip/ezdxf/cvxpy 等) + 本地模块拷贝 → BUILTIN 10→16
- **oracle 校验**: 把 skillsbench 官方参考解法喂给裸机 verifier, 51 个判负任务仅 13 个通过 → 38 个任务 verifier 在裸机深度不可用(lean4/maven 等工具链), 标签作废
- **容器化复核**: 对 5 个 FP 争议任务用官方 Dockerfile 构建 + 容器内重判 → **2 个翻案**(pddl-tpp-planning, flood-risk-analysis 的 agent 实际做对了, 裸机 verifier 误杀) → BUILTIN 16→18
- 最终有效仲裁集: 31 任务(内化 18 / 未内化 13, 类平衡), 其余 70 个标注"无法判定"剔除

## 最终仲裁(31 清洗任务)

| 方案/组合 | Acc | F1 | MCC | 备注 |
|----------|------|------|------|------|
| **S2+S4 融合** | **0.74** | **0.77** | **0.48** | 最优且跨规模稳定(23/101/31 任务三规模均 top) |
| S1+S3+S4 | 0.68 | 0.72 | 0.34 | |
| S4 单方案 | 0.65 | 0.59 | 0.40 | 最佳单方案, precision 0.78 |
| 基线(全判内化) | 0.52 | 0.71 | 0 | F1 相当但无判别力 |

## 核心结论

1. **零环境探测可行**: S2+S4 融合 MCC 0.48, 显著超越基线; 物料仅需 SKILL.md(任务名+标准用法代码), 无需环境/verifier/多轮执行。
2. **生成熵(S1)被证伪**, 任何 NLL 类指标必须配同构反事实/双中心化消除语言流利度先验。
3. **金标准质量决定仲裁可信度**: 同一批方案分数, 污染 GT 下 F1=0.31 → 清洗后 0.77。评测框架的 verifier 深度依赖容器, 裸机复现会产生大量假阴性。
4. S5 组级信号显著(t=9.11)但任务级分类贡献有限; 6+ 个真内化任务被不同方案分散命中 → 多方案融合必要。

## 文件

- `sglang_client.py` / `materials.py` / `schemes.py` / `run_all.py` — 探测管线(S1-S5, 并发)
- `gold_standard_par.py` — 并行金标准 runner(16 路 opencode)
- `oracle_check.py` / `container_recheck.py` / `rejudge.py` / `s5_adjust.py` — GT 质量控制工具
- 结果数据: `outputs/probe_lab/`(results.json, final_arbitration_v2.json, gt.json 等, 已 git add -f)
