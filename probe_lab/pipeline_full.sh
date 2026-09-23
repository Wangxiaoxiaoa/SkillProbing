#!/bin/bash
# 全链总控: Phase 0(等完成) → 1 oracle校准 → [质检] → 2 with/no-skill对照 → 3 SkillLearnBench → 4 汇总
# 质检门槛: oracle 合格率 <50% 则中止等待人工。全程日志落盘, 官方 jobs-dir 断点续跑。
export PATH=$HOME/.local/bin:$PATH
cd /data1/xiao/skillsbench
RUNS=/data1/xiao/SkillProbing/outputs/bench_runs
log() { echo "[$(date +%m-%d\ %H:%M)] $*" | tee -a /tmp/pipeline_full.log; }

log "等待 Phase 0 预构建完成..."
while pgrep -f prebuild_sb > /dev/null; do sleep 60; done
log "Phase 0 完成, 启动 Phase 1 oracle 全量校准(87任务, 并发6)"

uv run bench eval run --tasks-dir tasks --agent oracle --sandbox docker \
  --concurrency 6 --jobs-dir $RUNS/oracle_full \
  > $RUNS/oracle_full.log 2>&1
D=$(ls -dt $RUNS/oracle_full/*/ | head -1)
PASS=$(python3 -c "import json;s=json.load(open('$D/summary.json'));print(f\"{s['passed']}/{s['total']}\")")
PCT=$(python3 -c "import json;s=json.load(open('$D/summary.json'));t=s['total'];print(round(s['passed']/t*100))")
log "Phase 1 完成: oracle 通过 $PASS (合格率 ${PCT}%)"

if [ "$PCT" -lt 50 ]; then
  log "⚠️ oracle 合格率 <50%, 考场大面积异常, 中止等待人工检查: $D"
  exit 1
fi

log "Phase 2a: with-skill 全量(GLM@sglang, 官方任务自带skills)"
uv run bench eval run --tasks-dir tasks \
  --agent opencode --model glm53flash \
  --skill-mode with-skill --sandbox docker \
  --agent-env OPENAI_API_KEY=Tongxin123 \
  --agent-env OPENAI_BASE_URL=http://127.0.0.1:5919/v1 \
  --concurrency 16 --build-concurrency 8 \
  --jobs-dir $RUNS/glm_with_skill \
  > $RUNS/glm_with_skill.log 2>&1
log "Phase 2a 完成: $(grep -oE 'Score: [^,]*' $RUNS/glm_with_skill.log | tail -1)"

log "Phase 2b: no-skill 全量(对照)"
uv run bench eval run --tasks-dir tasks \
  --agent opencode --model glm53flash \
  --skill-mode no-skill --sandbox docker \
  --agent-env OPENAI_API_KEY=Tongxin123 \
  --agent-env OPENAI_BASE_URL=http://127.0.0.1:5919/v1 \
  --concurrency 16 --build-concurrency 8 \
  --jobs-dir $RUNS/glm_no_skill \
  > $RUNS/glm_no_skill.log 2>&1
log "Phase 2b 完成: $(grep -oE 'Score: [^,]*' $RUNS/glm_no_skill.log | tail -1)"

log "Phase 3: SkillLearnBench 对照(human_authored vs none)"
cd /data1/xiao/SkillLearnBench
export OPENAI_API_KEY=Tongxin123
export OPENAI_BASE_URL=http://127.0.0.1:5919/v1
uv run --with anthropic --with openai --with rich --with tomli --with dataclaw --with json-repair \
  python evaluate_skills.py --agent opencode --model glm53flash \
  --skill-path skills/human_authored none \
  > /data1/xiao/SkillProbing/outputs/bench_runs/slb.log 2>&1
log "Phase 3 完成"
log "Phase 4: 人工/脚本汇总仲裁(数据齐备, 本地计算)"

log "全链结束。GT 数据: $RUNS/{oracle_full,glm_with_skill,glm_no_skill}"
