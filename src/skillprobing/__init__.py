"""SkillProbing — 探测技能(skill)能力是否被模型内置。

核心思路: 对同一任务,分别"有 skill / 无 skill"驱动智能体执行,
  - 用 verifier 判结果正确性
  - 从 LLM logprobs 算输出 token 的熵/置信
对比两组差异 -> 判断该 skill 是否有效 / 是否被模型内置。

功能包:
  - gateway  : 截获智能体每次 LLM 输入/输出(含 logprobs)
  - skills   : 抽取/导入 skill+task+verifier(含用户自定义)
  - tasks    : 判题(verifier)
  - logits   : 每 token logprobs / 熵
  - run      : 驱动智能体
  - analysis : 有/无 skill 对比判定
  - workflow : 串起整个流程
"""
from skillprobing import gateway

__version__ = "0.2.0"

__all__ = ["gateway"]