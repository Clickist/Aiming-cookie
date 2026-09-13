#!/usr/bin/env python3
"""Aiming Cookie 诊断日志接收服务（部署在洛杉矶机 /opt/ac-logs）。

只监听 127.0.0.1:8787，公网入口是 Cloudflare Tunnel（logs.aimingcookie.com）。
零第三方依赖（Python 3.12 stdlib），由 systemd 单元 ac-logs.service 常驻。

防滥用四层（2026-09-13 点点拍板补齐；数字对真实用量留 >=100x 余量）：
  1. 幂等去重：同字节内容 sha256 命中近 1000 条 → 返回原编号，不重复落盘；
  2. 单 IP 限频：滑动窗口 10 次/小时（CF-Connecting-IP），超出 429 + Retry-After；
  3. 全局日额度：北京时间单日 200MB，超出 503 次日自恢复；incoming 总量 1GB 熔断；
     14 天前的旧包自动清理（启动时 + 每天一次）；
  4. CF 边缘限速（另一层，在 Cloudflare 控制台/API 配置，不在本文件）。

环境变量（/opt/ac-logs/ac-logs.env）：
  AC_LOGS_TOKEN          上传令牌，客户端请求头 X-AC-Token 必须匹配（必填）
  AC_LOGS_NOTIFY_WEBHOOK 通知钩子（预留，默认空）。非空时每收到一包 POST 一份 JSON 摘要。
"""

import hashlib
import json
import os
import threading
import time
import urllib.request
import secrets
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = ("127.0.0.1", 8787)
INCOMING_DIR = "/opt/ac-logs/incoming"
MAX_BODY = 10 * 1024 * 1024        # 单包上限：诊断包正常 <2MB
RATE_LIMIT = 10                     # 单 IP 每小时次数（真实用户每天 1-2 次）
RATE_WINDOW = 3600.0
DAILY_BYTE_CAP = 200 * 1024 * 1024  # 北京时间单日接收总量
TOTAL_BYTE_CAP = 1024 * 1024 * 1024 # incoming 目录总量熔断
PRUNE_AFTER = 14 * 86400            # 旧包保留 14 天
DEDUP_KEEP = 1000
TOKEN = os.environ.get("AC_LOGS_TOKEN", "")
NOTIFY_WEBHOOK_URL = os.environ.get("AC_LOGS_NOTIFY_WEBHOOK", "")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-AC-Token",
    "Access-Control-Max-Age": "86400",
}

_lock = threading.Lock()
_ip_hits: dict[str, deque[float]] = {}
_dedup: dict[str, tuple[str, float]] = {}  # sha256 -> (id, ts)
_daily_bytes = 0
_daily_key = ""  # 北京时间 YYYYMMDD


def beijing_day(now: float | None = None) -> str:
    return time.strftime("%Y%m%d", time.gmtime((now or time.time()) + 8 * 3600))


def dir_size(path: str) -> int:
    total = 0
    for name in os.listdir(path):
        try:
            total += os.path.getsize(os.path.join(path, name))
        except OSError:
            pass
    return total


def prune_old() -> None:
    cutoff = time.time() - PRUNE_AFTER
    try:
        for name in os.listdir(INCOMING_DIR):
            full = os.path.join(INCOMING_DIR, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass
    except OSError:
        pass


def prune_loop() -> None:
    while True:
        time.sleep(6 * 3600)
        prune_old()


def rate_limit_hit(ip: str) -> bool:
    """True = 本次被限频。滑动窗口计数，超窗口的旧时间戳顺手清掉。"""
    now = time.monotonic()
    hits = _ip_hits.setdefault(ip, deque())
    while hits and now - hits[0] >= RATE_WINDOW:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        return True
    hits.append(now)
    return False


def notify_async(summary: dict) -> None:
    """预留的通知钩子：webhook 非空则线程里 POST 摘要，失败只记日志不影响上传。"""
    if not NOTIFY_WEBHOOK_URL:
        return

    def _post():
        try:
            req = urllib.request.Request(
                NOTIFY_WEBHOOK_URL,
                data=json.dumps(summary, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:  # 通知失败绝不能影响接收主链路
            print(f"[notify] failed: {exc}", flush=True)

    threading.Thread(target=_post, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self):
        for key, value in CORS_HEADERS.items():
            self.send_header(key, value)

    def _reply(self, status: int, payload: dict | str, extra_headers=None,
               content_type="application/json"):
        body = payload if isinstance(payload, bytes) else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if content_type == "application/json" else str(payload).encode("utf-8")
        )
        self.send_response(status)
        self._cors()
        # 没读完请求体的错误路径必须关闭连接，否则残留字节会污染 keep-alive 的下一请求
        if status >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/healthz":
            self._reply(200, "ok", content_type="text/plain")
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        global _daily_bytes, _daily_key
        if self.path != "/upload":
            self._reply(404, {"error": "not found"})
            return
        if not TOKEN or self.headers.get("X-AC-Token", "") != TOKEN:
            self._reply(403, {"error": "forbidden"})
            return

        ip = self.headers.get("CF-Connecting-IP", "unknown")
        with _lock:
            limited = rate_limit_hit(ip)
        if limited:
            retry_after = 600
            print(f"[ratelimit] ip={ip}", flush=True)
            self._reply(429, {"error": "rate limited", "retry_after": retry_after},
                        extra_headers={"Retry-After": str(retry_after)})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._reply(413, {"error": "invalid or oversized body"})
            return
        body = self.rfile.read(length)
        digest = hashlib.sha256(body).hexdigest()

        today = beijing_day()
        with _lock:
            if _daily_key != today:
                _daily_key = today
                _daily_bytes = 0
            # 幂等去重：同内容近 1000 条内直接复用编号
            hit = _dedup.get(digest)
            if hit is not None:
                hit_id = hit[0]
                _dedup[digest] = (hit_id, time.time())
                print(f"[dedup] ip={ip} id={hit_id}", flush=True)
                duplicate = True
            else:
                duplicate = False

        if duplicate:
            self._reply(200, {"id": hit_id, "duplicate": True})
            notify_async({"id": hit_id, "duplicate": True})
            return

        if _daily_bytes + length > DAILY_BYTE_CAP:
            print(f"[quota] daily cap hit ({_daily_bytes} bytes today)", flush=True)
            self._reply(503, {"error": "daily quota exceeded, try tomorrow"})
            return
        if dir_size(INCOMING_DIR) + length > TOTAL_BYTE_CAP:
            print("[quota] total cap hit, needs manual cleanup", flush=True)
            self._reply(503, {"error": "storage full, contact support"})
            return

        try:
            bundle = json.loads(body.decode("utf-8"))
            if not isinstance(bundle, dict) or "schema" not in bundle:
                raise ValueError("missing schema")
            app_version = str(bundle.get("app_version", "unknown"))
        except Exception:
            self._reply(400, {"error": "body must be a capture diagnostics bundle JSON"})
            return

        stem = beijing_day() + "-" + time.strftime("%H%M%S") + "-" + secrets.token_hex(4)
        final_path = os.path.join(INCOMING_DIR, stem + ".json")
        tmp_path = final_path + ".tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(body)
        os.replace(tmp_path, final_path)  # 原子落盘，不会出现半个包

        with _lock:
            _daily_bytes += length
            _dedup[digest] = (stem, time.time())
            if len(_dedup) > DEDUP_KEEP:
                for key in sorted(_dedup, key=lambda k: _dedup[k][1])[: len(_dedup) - DEDUP_KEEP]:
                    del _dedup[key]
        os.chmod(final_path, 0o600)
        print(f"[upload] id={stem} bytes={len(body)} app={app_version} ip={ip}", flush=True)
        notify_async({"id": stem, "bytes": len(body), "app_version": app_version})
        self._reply(200, {"id": stem, "bytes": len(body)})

    def log_message(self, fmt, *args):  # 静默默认访问日志，只留有意义的行
        pass


def main():
    if not TOKEN:
        raise SystemExit("AC_LOGS_TOKEN is not set")
    os.makedirs(INCOMING_DIR, exist_ok=True)
    global _daily_bytes, _daily_key
    _daily_key = beijing_day()
    _daily_bytes = dir_size(INCOMING_DIR)  # 重启后从磁盘恢复当日口径（保守取全目录）
    prune_old()
    threading.Thread(target=prune_loop, daemon=True).start()
    server = ThreadingHTTPServer(LISTEN, Handler)
    print(
        f"[ac-logs] listening on {LISTEN[0]}:{LISTEN[1]} "
        f"(rate {RATE_LIMIT}/h/ip, daily {DAILY_BYTE_CAP // (1024 * 1024)}MB, "
        f"start_bytes={_daily_bytes})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
