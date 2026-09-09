"""SkillProbing — 探测"技能能力是否被大模型内置"的工具库。

核心第一步:从智能体中获取每次调用 LLM 的完整输入/输出内容。
采用【方案1: 网关截获】(源自 trajectory-extractor):
  把 agent 的 model base_url 指向本网关,网关拦截/转发每次 LLM 请求,
  并把完整 request(含 messages/工具结果) + response(模型输出/工具调用)
  记录为轨迹文件。

后续分析(熵对比 skill有/无/替换)在 skillprobing.analysis 中演进。
"""
from skillprobing import gateway

__version__ = "0.1.0"

__all__ = ["gateway"]