"""SkillProbing CLI — 从智能体截获每次 LLM 输入/输出(网关方案)。"""
from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillprobing")
    sub = p.add_subparsers(dest="cmd", required=True)

    pg = sub.add_parser("gateway", help="方案1: 网关截获每次 LLM 输入/输出并记录轨迹")
    pg.add_argument("--listen", default="127.0.0.1:5909", help="监听地址")
    pg.add_argument("--upstream", required=True, help="上游推理服务地址, 如 https://host:port")
    pg.add_argument("--key", default="", help="上游 API key")
    pg.add_argument("--record", required=True, help="轨迹输出文件(jsonl)")
    pg.set_defaults(func=_cmd_gateway)
    return p


def _cmd_gateway(args) -> int:
    from skillprobing.gateway import run_gateway_server

    run_gateway_server(listen=args.listen, upstream=args.upstream, api_key=args.key,
                       record_path=args.record)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())