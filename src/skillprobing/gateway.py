"""方案1 — 网关截获 LLM 调用获取轨迹(ptdl:拦截 agent 的 LLM 请求转发并记录)。"""
from __future__ import annotations

import json, ssl, sys, time, urllib.error, urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_records: list[dict[str, Any]] = []


@dataclass
class GatewayConfig:
    upstream_base_url: str
    api_key: str = ""
    record_out: str | None = None
    listen: str = "127.0.0.1:5909"


def _append(path: str, rec: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"record fail: {e}\n")


def record_trace(cfg: GatewayConfig, method, path, req_json, status, resp_obj):
    rec = {"ts": time.time(), "method": method, "path": path, "request": req_json,
           "response_status": status, "response": resp_obj}
    _records.append(rec)
    if cfg.record_out:
        _append(cfg.record_out, rec)
    return rec


def forward(upstream, req_path, method, headers, body, api_key=""):
    url = upstream.rstrip("/") + req_path
    hdrs = {k: v for k, v in (headers or {}).items()
            if k.lower() not in ("host", "content-length", "accept-encoding")}
    hdrs["Content-Type"] = "application/json"
    if api_key:
        hdrs["Authorization"] = f"Bearer {api_key}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, data=body or None, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
            return {"status": resp.status, "content": resp.read(), "headers": dict(resp.headers)}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "content": exc.read(), "headers": dict(exc.headers), "error": str(exc)}
    except Exception as exc:
        return {"status": 502, "content": json.dumps({"error": str(exc)}).encode(), "headers": {},
                "error": str(exc)}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _cfg: GatewayConfig = None

    def log_message(self, *a):
        pass

    def _dispatch(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            req_json = json.loads(body) if body else {}
        except Exception:
            req_json = {}
        result = forward(self._cfg.upstream_base_url, self.path, self.command,
                         dict(self.headers), body, api_key=self._cfg.api_key)
        self.send_response(result["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result["content"])))
        self.end_headers()
        self.wfile.write(result["content"])
        resp_text = result["content"].decode("utf-8", errors="replace")
        try:
            resp_obj = json.loads(resp_text)
        except Exception:
            resp_obj = {"raw_text": resp_text}
        record_trace(self._cfg, self.command, self.path, req_json, result["status"], resp_obj)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()


def run_gateway_server(*, listen="127.0.0.1:5909", upstream, api_key="", record_path):
    cfg = GatewayConfig(upstream_base_url=upstream, api_key=api_key, record_out=record_path, listen=listen)
    _Handler._cfg = cfg
    host, port = listen.split(":")
    srv = ThreadingHTTPServer((host, int(port)), _Handler)
    sys.stderr.write(f"[gateway] {host}:{port} -> {upstream}\nrecording -> {record_path}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def load_records(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out