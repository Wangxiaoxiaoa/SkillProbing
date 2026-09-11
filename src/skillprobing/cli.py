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
        custom_root=args.custom_root,
    )
    for r in results:
        print(f"[{r['task_id']}] verdict={r['verdict']} Δacc={r.get('accuracy_delta')} "
              f"ΔPPL={r.get('ppl_delta')} critKL={r.get('critical_kl')} | {r['reason']}")
    print(f"\n汇总已写: outputs/summary.json")
    return 0


def _cmd_validate(args) -> int:
    from pathlib import Path
    from skillprobing.config import load_settings
    from skillprobing.analysis.validate import evaluate, build_table, save_table

    cfg = load_settings()
    task_dir = Path(args.task_dir)
    ev = evaluate(
        task_dir=task_dir,
        with_trace=Path(args.with_trace) if args.with_trace else None,
        without_trace=Path(args.without_trace) if args.without_trace else None,
        cfg=cfg.probe,
        task_id=task_dir.name,
    )
    table = build_table([ev])
    out = Path(args.output) if args.output else Path("outputs/validation_table.json")
    save_table(table, out)
    print(f"skill: {ev.skill_name}")
    print(f"  熵判断: {ev.entropy_verdict} ({ev.entropy_reason})")
    print(f"  金标准: {ev.verifier_verdict}")
    print(f"表格已写: {out} / {out.with_suffix('.csv')}")
    return 0


def _cmd_gateway(args) -> int:
    from skillprobing.gateway import run_gateway_server

    run_gateway_server(listen=args.listen, upstream=args.upstream,
                       api_key=args.key, record_path=args.record,
                       logprobs=args.logprobs, stream=args.stream)
    return 0


def _cmd_docker_run(args) -> int:
    from pathlib import Path
    from skillprobing.runner.docker_runner import run_with_and_without_skill

    task_dir = Path(args.task_dir)
    out = Path(args.output)
    r_with, r_without = run_with_and_without_skill(
        task_dir, out,
        gateway_url=args.gateway or "http://127.0.0.1:5909/v1",
        timeout=args.timeout,
    )
    print(f"with_skill: rc={r_with.returncode} workspace={r_with.workspace}")
    print(f"without_skill: rc={r_without.returncode} workspace={r_without.workspace}")
    if r_with.verifier:
        print(f"with verifier: passed={r_with.verifier.passed} detail={r_with.verifier.detail}")
    if r_without.verifier:
        print(f"without verifier: passed={r_without.verifier.passed} detail={r_without.verifier.detail}")
    print(f"logs: {out}/with_skill/container.stdout (stderr)")
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

    pd = sub.add_parser("docker-run", help="在 Docker 容器内运行 task(与 skillsbench 对齐)")
    pd.add_argument("--task-dir", "-t", required=True, help="任务目录")
    pd.add_argument("--gateway", "-g", default="http://127.0.0.1:5909/v1", help="网关URL")
    pd.add_argument("--output", "-o", default="outputs/docker_run", help="输出目录")
    pd.add_argument("--timeout", type=int, default=600, help="单个容器超时(秒)")
    pd.set_defaults(func=_cmd_docker_run)

    pv = sub.add_parser("validate", help="生成对照表: 熵判断结果 vs verifier金标准(两者独立)")
    pv.add_argument("--task-dir", "-t", required=True, help="已抽取任务目录")
    pv.add_argument("--with-trace", default=None, help="有skill轨迹文件(可选)")
    pv.add_argument("--without-trace", default=None, help="无skill轨迹文件(可选)")
    pv.add_argument("--output", "-o", default="outputs/validation_table.json", help="输出JSON/CSV路径")
    pv.set_defaults(func=_cmd_validate)
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())