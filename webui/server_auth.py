# -*- coding: utf-8 -*-
"""服务器模式鉴权（MUSE_SERVER_MODE=1 时启用）。

本地模式（默认）保持原样：无登录，直接可用。
服务器模式启用后：
  - 除 /api/health、/login 外全部接口都要登录；
  - 账号密码只从环境变量读（MUSE_ADMIN_USER / MUSE_ADMIN_PASSWORD），
    不写进仓库、不进数据库；
  - 会话是 HMAC-SHA256 签名的 cookie（30 天），密钥来自
    MUSE_SESSION_SECRET；两个值缺失或太短时启动即报错，不带病运行；
  - 登录输错 5 次锁 5 分钟（按客户端 IP 计）。

反向代理部署时请在代理层再做 HTTPS；本模块只解决“谁能进控制台”。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

from flask import jsonify, redirect, request

logger = logging.getLogger(__name__)

_COOKIE_NAME = "muse_server_session"
_SESSION_TTL_SECONDS = 30 * 24 * 3600
_MAX_FAILURES = 5
_LOCK_SECONDS = 300

# ip -> [失败次数, 锁定到期时间戳]
_failures: dict[str, list] = {}

# 服务端会话注册表：session_id -> 过期时间戳。只存在内存里，
# 服务重启后全部会话失效（需重新登录），登出时立即吊销。
_sessions: dict[str, float] = {}

_LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Muse Register — 登录</title>
<style>body{font-family:system-ui,sans-serif;background:#111827;color:#e5e7eb;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:28px 32px;width:320px}
h1{font-size:18px;margin:0 0 4px}.sub{color:#9ca3af;font-size:13px;margin:0 0 18px}
label{display:block;font-size:13px;color:#d1d5db;margin:12px 0 4px}
input{width:100%;box-sizing:border-box;padding:9px 10px;border-radius:8px;border:1px solid #4b5563;background:#111827;color:#f9fafb;font-size:14px}
button{width:100%;margin-top:18px;padding:10px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-size:15px;cursor:pointer}
.err{color:#fca5a5;font-size:13px;margin-top:12px;min-height:18px}</style></head>
<body><div class="card"><h1>Muse Register</h1><p class="sub">服务器模式 · 请登录后使用</p>
<form method="post" action="/login"><label>账号</label><input name="username" autocomplete="username" required>
<label>密码</label><input name="password" type="password" autocomplete="current-password" required>
<button type="submit">登录</button><div class="err">__ERROR__</div></form></div></body></html>"""


def is_server_mode() -> bool:
    return os.environ.get("MUSE_SERVER_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def data_dir() -> Path:
    """状态目录：服务器模式用 MUSE_DATA_DIR，否则用仓库内 data/。"""
    override = os.environ.get("MUSE_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data"


def env_file_path() -> Path:
    """WebUI 写配置的目标 .env：服务器模式写进数据卷，否则写仓库根目录。"""
    if is_server_mode():
        return data_dir() / ".env"
    return Path(__file__).resolve().parent.parent / ".env"


def server_settings() -> tuple[str, str, str]:
    """返回 (admin_user, admin_password, session_secret)，缺失即报错。"""
    user = os.environ.get("MUSE_ADMIN_USER", "admin").strip() or "admin"
    password = os.environ.get("MUSE_ADMIN_PASSWORD", "")
    secret = os.environ.get("MUSE_SESSION_SECRET", "")
    if not password or len(password) < 8:
        raise RuntimeError("服务器模式要求 MUSE_ADMIN_PASSWORD（至少 8 位），请在 .env.server 里设置后重启")
    if not secret or len(secret) < 16:
        raise RuntimeError("服务器模式要求 MUSE_SESSION_SECRET（至少 16 位），请在 .env.server 里设置后重启")
    return user, password, secret


def _new_session(username: str, secret: str) -> tuple[str, str]:
    """创建会话：返回 (cookie 值, session_id)。"""
    import secrets

    sid = secrets.token_urlsafe(24)
    expires = int(time.time()) + _SESSION_TTL_SECONDS
    payload = f"{username}.{expires}.{sid}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    _sessions[sid] = float(expires)
    cookie = base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()
    return cookie, sid


def _verify_session(cookie: str | None, secret: str) -> str | None:
    if not cookie:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cookie.encode()).decode()
        username, expires, sid, sig = decoded.rsplit(".", 3)
        if int(expires) < int(time.time()):
            _sessions.pop(sid, None)
            return None
        expect = hmac.new(secret.encode(), f"{username}.{expires}.{sid}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        # 服务端注册表：登出/重启后即吊销，光验签名不够。
        if _sessions.get(sid) != float(expires):
            return None
        return username
    except Exception:
        return None


def _revoke_session(cookie: str | None) -> None:
    if not cookie:
        return
    try:
        decoded = base64.urlsafe_b64decode(cookie.encode()).decode()
        _sessions.pop(decoded.rsplit(".", 3)[2], None)
    except Exception:
        pass


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _is_locked(ip: str) -> bool:
    entry = _failures.get(ip)
    if not entry:
        return False
    _, locked_until = entry
    if locked_until and time.time() < locked_until:
        return True
    if locked_until and time.time() >= locked_until:
        _failures.pop(ip, None)
    return False


def _record_failure(ip: str) -> None:
    count, _ = _failures.get(ip, [0, 0])
    count += 1
    locked_until = time.time() + _LOCK_SECONDS if count >= _MAX_FAILURES else 0
    _failures[ip] = [count, locked_until]
    if locked_until:
        logger.warning("[ServerAuth] %s 登录失败 %d 次，已锁定 %d 秒", ip, count, _LOCK_SECONDS)


def register_server_auth(app) -> None:
    """在 create_app() 里调用；配置缺失时启动即报错。"""
    admin_user, admin_password, secret = server_settings()
    logger.info("[ServerAuth] 服务器模式已启用，登录账号: %s", admin_user)

    @app.before_request
    def _guard():
        if request.path in ("/api/health", "/login", "/favicon.ico"):
            return None
        if request.path.startswith("/static/"):
            return None
        if _verify_session(request.cookies.get(_COOKIE_NAME), secret):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "未登录"}), 401
        return redirect("/login")

    @app.get("/login")
    def _login_page():
        # 不用 str.format：模板里的 CSS 大括号会被当成占位符。
        return _LOGIN_HTML.replace("__ERROR__", ""), 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.post("/login")
    def _login_submit():
        ip = _client_ip()
        if _is_locked(ip):
            return _LOGIN_HTML.replace("__ERROR__", "失败次数过多，请 5 分钟后再试"), 429, {"Content-Type": "text/html; charset=utf-8"}
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == admin_user and hmac.compare_digest(password, admin_password):
            _failures.pop(ip, None)
            cookie, _sid = _new_session(username, secret)
            resp = redirect("/")
            resp.set_cookie(
                _COOKIE_NAME,
                cookie,
                max_age=_SESSION_TTL_SECONDS,
                httponly=True,
                samesite="Lax",
            )
            return resp
        _record_failure(ip)
        return _LOGIN_HTML.replace("__ERROR__", "账号或密码错误"), 401, {"Content-Type": "text/html; charset=utf-8"}

    @app.post("/api/logout")
    def _logout():
        _revoke_session(request.cookies.get(_COOKIE_NAME))
        resp = jsonify({"ok": True})
        resp.delete_cookie(_COOKIE_NAME)
        return resp
