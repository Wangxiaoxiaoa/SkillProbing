"""SkillProbing CLI。

命令:
  skillprobing fetch-skills [task_id...]         # 从 skillsbench 抽取 skills
  skillprobing compare --gateway <url> [task_ids] # 对已抽取/自定义任务跑有/无skill对比
  skillprobing compare --custom-root <dir>        # 用用户自定义任务
"""
from __future__ import annotations

import argparse
import json


def _cmd_fetch(args) -> int:
    from skillprobing.skills.skill_loader import extract_tasks
    from skillprobing.config import SkillsConfig

    specs = extract_tasks(SkillsConfig(), args.tasks or None)
    print(f"抽取 {len(specs)} 个 task 到 {SkillsConfig().skills_dir}")
    return 0


def _cmd_run(args) -> int:
    from skillprobing.config import load_settings
    from skillprobing.workflow.run_compare import Compare

    results = Compare(load_settings()).run(
        task_ids=args.tasks or None,
        gateway_url=args.gateway,
        custom_root=args.task if hasattr(args, "task") and args.task else None,
    )
    for r in results:
        print(f"[{r['task_id']}] verdict={r['verdict']} Δacc={r['accuracy_delta']} Δ熵={r['entropy_delta']} | {r['reason']}")
    print(f"\n汇总已写: outputs/summary.json")
    return 0


def _cmd_gateway(args) -> int:
    from skillprobing.gateway import run_gateway_server

    run_gateway_server(listen=args.listen, upstream=args.upstream,
                       api_key=args.key, record_path=args.record,
                       logprobs=args.logprobs, stream=args.stream)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillprobing")
    sub = p.add_subparsers(dest="cmd", required=True)

    pg = sub.add_parser("gateway", help="启动网关截获智能体 LLM 输入/输出+logprobs")
    pg.add_argument("--listen", default="127.0.0.1:5909")
    pg.add_argument("--upstream", required=True)
    pg.add_argument("--key", default="")
    pg.add_argument("--record", required=True)
    pg.add_argument("--logprobs", type=int, default=8, help="每个token取的topK logprobs(0=关闭)")
    pg.add_argument("--stream", action="store_true", help="保持流式(否则强制非流式以拿logprobs)")
    pg.set_defaults(func=_cmd_gateway)

    pf = sub.add_parser("fetch-skills")
    pf.add_argument("tasks", nargs="*")
    pf.set_defaults(func=_cmd_fetch)

    pr = sub.add_parser("run")
    pr.add_argument("--gateway", default=None, help="网关URL(截获logprobs)")
    pr.add_argument("--task-dir", "-t", dest="custom_root", default=None, help="用户自定义任务目录")
    pr.add_argument("tasks", nargs="*")
    pr.set_defaults(func=_cmd_run)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())