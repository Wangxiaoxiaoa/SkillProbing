# SkillProbing 项目全记录：思路、实现、问题与教训

> 更新: 2026-09-23 ｜ 目标模型: GLM-5.3-Flash (glm53flash@5919, sglang, 本机)

---

## 一、研究目标与方案

### 要回答的问题
给模型挂上一个 skill（SKILL.md 形式的技能文档）后：
1. 这个 skill **是否起作用**（挂上有没有提升）？
2. 模型**是否已经内化**了这个 skill（不挂也会）？

### 两类方法
| 方法 | 做法 | 优缺点 |
|------|------|--------|
| **标准方法（金标准）** | 同一 agent+模型，任务跑两遍（带/不带 skill），对照通过率 | 准确；但真实场景无法为每个 skill 造验证器 → 不泛化 |
| **本项目方案（探测法）** | 零环境，只分析模型输出 token 概率分布 | 便宜可泛化；但需要证明判得准 |

### 探测方案（S1–S5）
| 方案 | 思路 | 状态 |
|------|------|------|
| S1 生成熵 | 盲测任务名，看自由生成 token 的平均熵 | ❌ **证伪**（熵低只反映措辞模板性，F1=0） |
| S2 NLL 反事实对照 | SKILL.md 提取标准用法代码(gold trace) vs 同构反事实(标识符替换)，prefill NLL 对比 | ✅ 核心方案 |
| S3 上下文依赖 | 同一 trace 有/无 skill 提示的 NLL 差，差小=内化 | ✅ 辅助 |
| S4 工具名盲测 | "该任务最合适的工具" top-k 是否偏好本 skill 工具名 | ✅ 最佳单方案 |
| S5 交叉匹配 | 任务×轨迹 N×N 矩阵，**双中心化**后对角线检验 | ✅ 组级信号最强(t=9~11) |

**方法论教训**：
1. 生成熵不可用——低熵只反映语言流畅度，不反映技能内化
2. 任何 NLL 类指标必须配**同构反事实**（S2）或**双中心化**（S5），否则测到的是"见过类似语料"
3. 探测粒度必须与技能粒度对齐（skillsbench 任务↔skill 一一对应 → 有效；DS-1000 隐式库熟练度 → 失效）

---

## 二、金标准 Benchmark 的寻找（踩坑史）

**需求**：每个任务原生带 skill 文档 + 自动判分器 + 支持带/不带对照。

| Benchmark | 结论 | 原因 |
|-----------|------|------|
| **skillsbench** (1807⭐) | ✅ 主战场 | 唯一原生"每任务 SKILL.md + verifier" |
| **SkillLearnBench** (COLM'26) | ✅ 第二金标准 | 100 已验证实例 + 36 人工 SKILL.md + 官方 none 基线 |
| DS-1000 | ❌ 删除 | 隐式库熟练度，粒度错配（题级 GT vs 库级探测） |
| BFCL (gorilla 13037⭐) | ❌ 删除 | 函数调用能力，skill 注入实测零增益(-0.4pp) |
| KernelBench | ❌ 删除 | 同粒度错配 |
| tau-bench | ❌ 删除 | skill 是域级非任务级；金标准需 LLM 用户模拟器 |
| OSWorld / SWE-bench / toolsandbox | ❌ 删除 | GUI 判分/成本/无原生 skill |

**关键认知**：公开世界里"每任务带 skill+verifier"的 benchmark 目前只有 skillsbench 一个（这正是其价值）。SkillLearnBench 是新发现的第二个（COLM'26，100 实例，官方原生 with/none 对照设计）。

---

## 三、skillsbench 官方方案接入（重要教训）

### 教训：不要裸机跑，必须按官方容器方案

skillsbench 官方设计 = 每任务一个 Dockerfile 容器（含全部依赖）→ agent/判分都在容器内。
**裸机跑的问题链**（62% 任务读数不可信）：
1. agent 执行缺依赖（qutip/ezdxf/torch 等）→ 产物错误
2. verifier 缺 pytest/uv → 直接崩或读数漂移
3. oracle（官方标准解法）在裸机只有 13/51 通过 → 无法校准

**裸机时代产物已废弃**：outputs/gold_standard 裸机数据删除，git 有摘要备份。

### 官方方案接入的正确姿势（当前路径）
```bash
uv tool install benchflow        # 官方 CLI (bench, v0.7.8)
uv sync --locked                 # 仓库工具
bench tasks check tasks/<t>      # 任务校验
bench eval run --tasks-dir tasks/<t> \
  --agent oracle --sandbox docker --sandbox-user root   # oracle 校准
bench eval run --tasks-dir tasks/<t> \
  --agent opencode --model glm53flash \
  --skill-mode with-skill|no-skill --sandbox docker      # GLM 对照
```
**注意**: `--tasks-dir` 只支持单任务目录（多任务父目录模式会全 errored）；`--sandbox-user root` 在部分场景触发 benchflow hardening bug，默认 agent 用户 + 镜像内依赖预装更稳。

### 网络适配（本机环境三层问题与解法）
| 问题 | 根因 | 解法 |
|------|------|------|
| GitHub 下载卡死 | 容器 bridge 网络直连 GitHub 不通 | **宿主 ssh 隧道**：`ssh -fN -L 0.0.0.0:18080:127.0.0.1:5903 admin@47.93.196.89`（公网 5903 mihomo 代理），compose 模板注入 `HTTPS_PROXY=http://172.17.0.1:18080` |
| apt 明文 http 源 502 | mihomo 对 http 明文代理返回 502 | Dockerfile ENV 只保留 https_proxy，http 置空 + NO_PROXY |
| pypi 经隧道 SSL EOF | 隧道节点对 pypi.org TLS 不稳定 | `UV_DEFAULT_INDEX/PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`（直连稳定） |
| uv/uvx 安装 | 综合以上 | **最终方案：COPY 宿主二进制进镜像**（/home/uos/.local/bin/uv，零网络，已 commit） |

**compose 模板适配位置**（.venv 内 benchflow，本机适配非上游修改）：
`skillsbench/.venv/lib/python3.12/site-packages/benchflow/sandbox/_compose_files/docker-compose-build.yaml` 的 build args。

### 已验证里程碑
- ✅ bench tasks check 通过
- ✅ offer-letter-generator oracle **100% PASS**（官方全链路首次打通）
- ✅ 3d-scan-calc oracle **100% PASS**（根因闭合后第二个任务）
- ⚠️ tasks/ 的 Dockerfile 曾出现"双补丁残留"（pip 层+COPY 层并存，pip 层失败拖垮 build）→ 已清理并 commit

---

## 四、SkillsBench 金标准第一次结果（裸机时代，已被官方方案取代）

- 101 任务：BUILTIN 18 / NOT_BUILTIN 2 / ABSENT 83（含大量裸机环境假阴性）
- 探测方案仲裁：S2+S4 融合 F1 0.77 / MCC 0.48（清洗后 31 任务）
- **教训**：金标准质量决定仲裁可信度（污染 GT 下 F1 0.31 → 清洗后 0.77）
- 数据保留：`data/skillsbench_final/`（skillsbench_results.json 等）

---

## 五、当前进行中的事（按任务编排的新管线）

### 架构转变（用户提出，正确）
按**任务**编排而非按**阶段**编排：
```
每任务一个容器生命周期:
  build(1次) → oracle 校准 → 不合格则跳过 GLM
  → 合格 → 重置工作区 → GLM with-skill → 判分
  → 重置工作区 → GLM without-skill → 判分 → 销毁容器
```
优点: build 一次用三次；oracle 不合格的任务直接省掉 2 次 GLM 执行；with/without 同环境对照更公平。
实现: `probe_lab/task_pipeline.py`（3 路并发，断点续跑，输出 outputs/task_pipeline/results.json）

### 当前卡点（最后更新时）
- task_pipeline.py 试点 3d-scan-calc 时 **build 失败: docker API permission denied**
- 原因: python subprocess 直接调 docker（uos 不在 docker 组的实际执行环境中）
- **修复方案（未完成）**: 所有 docker 命令经 `sg docker -c` 包裹（已改 sh() 但试点未重跑）
- **用户约束**: 严格按官方方案构建，sudo 密码 uos321$%（仅本项目测试用），**不要重启 docker**（有其他服务），**不要 sudo 删除无关内容**

---

## 六、SkillLearnBench 环境状态
| 组件 | 状态 |
|------|------|
| Python 依赖 | ✅（清华镜像拉取） |
| opencode 注册 | ✅（agents/__init__.py 加了 opencode 条目） |
| .env | ✅（GLM@sglang 配置） |
| 任务镜像 build | ❌ 试点失败（COPY uv 层与 build context 冲突: /skills not found——uv 二进制文件位置需放对，未修） |

---

## 七、待办（等指令）
1. task_pipeline.py 试点重跑（3d-scan-calc，验证 sg docker 修复）
2. 试点通过 → 全量 87 任务跑金标准（with/without + oracle）
3. SkillLearnBench 镜像 build 修复 → 同样流程
4. 双金标准 GT → 探测分数仲裁 → 最终报告
