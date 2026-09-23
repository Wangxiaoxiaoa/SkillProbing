#!/bin/bash
# 等待 Phase 0 完成 → 自动启动 Phase 1 oracle 全量校准
while pgrep -f prebuild_sb > /dev/null; do sleep 60; done
echo "[$(date +%H:%M)] Phase 0 预构建完成, 启动 Phase 1 oracle 校准"
cd /data1/xiao/skillsbench
export PATH=$HOME/.local/bin:$PATH
uv run bench eval run \
  --tasks-dir tasks \
  --agent oracle --sandbox docker \
  --concurrency 6 \
  --jobs-dir /data1/xiao/SkillProbing/outputs/bench_runs/oracle_full \
  > /data1/xiao/SkillProbing/outputs/bench_runs/oracle_full.log 2>&1
echo "[$(date +%H:%M)] Phase 1 完成: $(tail -3 /data1/xiao/SkillProbing/outputs/bench_runs/oracle_full.log | grep -oE 'Score: [^,]*' | head -1)"
