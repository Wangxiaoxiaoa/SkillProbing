# SkillProbing

**探测「技能(skill)能力是否已被大模型内置」的工具库。**

核心思路: skill 本质是附加到上下文的文字指令。如果模型已经"内置/内化"了某个技能,
那么即使把 skill 从输入移除或换成其他文本,模型在对应任务上的**行为/输出分布也会接近**。
SkillProbing 通过**对比输出 token logits 的熵变化**来量化这种"内置程度"。

## 核心功能

### 第一步(当前已实现): 从智能体获取每次 LLM 调用的完整输入/输出

采用**方案1 —— 网关截获**(源自 `/mnt/data/xiao/self_skill/trajectory-extractor`):

把 agent(如 opencode)的 model `base_url` 指向本网关,网关拦截、转发每次对推理
服务(sglang/vLLM/OpenAI 兼容)的请求,并把**完整 `request`(含 messages、工具结果)
+ `response`(模型输出、工具调用、reasoning)**记录为轨迹 jsonl。

这样才能拿到做 logits/熵分析所需的"逐次调用的输入与输出"。

#### 用法(CLI)

```bash
# 启动网关,把 agent 的模型指向 http://127.0.0.1:5909,轨迹写入 traj.jsonl
skillprobing gateway \
  --upstream https://101.89.57.41:5912 \
  --key 'Tongxin123.' \
  --record ./traj.jsonl \
  --listen 127.0.0.1:5909
```

然后配置你的 agent(如 opencode) 的 model base_url 为 `http://127.0.0.1:5909`,
运行任务后,`traj.jsonl` 即含每一次 LLM 调用的完整输入输出。

#### 轨迹格式(每条一行)

```json
{
  "ts": 1788946413.1,
  "method": "POST",
  "path": "/v1/chat/completions",
  "request":  {"model": "dsv4-flash", "messages": [...]},
  "response_status": 200,
  "response": {"choices": [...], "usage": {...}}
}
```
流式(SSE)调用时 `response` 为 `{"raw_text": "<SSE片段流>"}`,可用
`load_records()` 读取后再拼接/解析。

## 后续(规划)

- 分析层: 对比 **有无 skill**、**skill 内容替换** 两种情况下,模型输出 token 的
  logits 熵;熵趋于一致则认为该 skill 能力已被模型内置。
- 依赖: 从带 logprobs 的推理后端(未开投机解码的 sglang/vLLM)获取每个 token logits。

## 进度

- [x] 第一步: 网关截获每次 LLM 输入/输出(方案1)
- [ ] 日志解析与会话重建
- [ ] 熵对比分析(skill 有/无/替换)