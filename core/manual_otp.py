# -*- coding: utf-8 -*-
"""手动验证码通道（纯邮箱来源用）。

背景：`core/email_provider.py` 的 `wait_for_otp` 在 `USE_EMAIL_SERVICE=False`
时就引用了这个模块，但文件一直缺失，走到手动分支会直接 ImportError。
纯邮箱（一行一个、无取码凭证）没有可自动拉取的邮箱服务，验证码只能由
用户在 WebUI 里手动输入——这个模块就是那条通道：

  - 注册任务等码时：`wait_for_manual_otp(email, timeout, job_id)` 轮询等待；
  - 用户在任务列表输入验证码后：`POST /api/jobs/<id>/otp` 调
    `submit_manual_otp(email, code, job_id)` 把码递进来；
  - 等待循环收到码即返回，超时抛 TimeoutError，任务被停止时抛 StopRequested。

只存内存：验证码 10 分钟过期；进程重启后等待中的任务本来就会被标记为
中断，内存状态丢了正好一致，不需要落库。
"""
from __future__ import annotations

import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

_CODE_TTL_SECONDS = 600
_POLL_SECONDS = 2.0

_lock = threading.Lock()
# email(lower) -> (code, expires_at)
_codes: dict[str, tuple[str, float]] = {}
# email(lower) -> {"job_id": int|None, "since": float}
_waiting: dict[str, dict] = {}


def _normalize_code(raw: str | None) -> str:
    """验证码只保留数字，接受 4-8 位（Muse 目前是 6 位）。"""
    code = re.sub(r"\D", "", str(raw or ""))
    if not (4 <= len(code) <= 8):
        raise ValueError("验证码应为 4-8 位数字")
    return code


def submit_manual_otp(email: str, code: str, job_id: int | None = None) -> dict:
    """用户在 WebUI 提交验证码。返回 {"ok": True} 或 {"ok": False, "error": ...}。"""
    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("邮箱地址为空")
    try:
        clean = _normalize_code(code)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    with _lock:
        _codes[email] = (clean, time.time() + _CODE_TTL_SECONDS)
    logger.info("[ManualOTP] 收到 %s 的手动验证码（任务 #%s）", email, job_id)
    return {"ok": True}


def wait_for_manual_otp(
    email: str,
    timeout: int = 180,
    job_id: int | None = None,
    stop_check=None,
) -> str:
    """阻塞等待用户提交验证码；超时抛 TimeoutError，被停止抛 StopRequested。"""
    email = str(email or "").strip().lower()
    deadline = time.time() + max(1, int(timeout or 180))
    with _lock:
        _waiting[email] = {"job_id": job_id, "since": time.time()}
    logger.info(
        "[ManualOTP] 等待手动验证码：%s（任务 #%s，最长 %d 秒）——请在 WebUI 任务列表输入邮箱收到的验证码",
        email, job_id, int(timeout or 180),
    )
    try:
        while True:
            if stop_check is not None:
                # 约定与 registration_service.check_stop 一致：
                # 已停止时抛 StopRequested，否则静默返回。
                stop_check()
            with _lock:
                entry = _codes.pop(email, None)
                if entry is not None:
                    code, expires_at = entry
                    if time.time() <= expires_at:
                        logger.info("[ManualOTP] %s 验证码已收到", email)
                        return code
            if time.time() >= deadline:
                raise TimeoutError(f"等待手动验证码超时（{int(timeout or 180)} 秒）：{email}")
            time.sleep(_POLL_SECONDS)
    finally:
        with _lock:
            _waiting.pop(email, None)


def list_waiting() -> list[dict]:
    """当前正在等手动验证码的任务（供排障/状态接口用）。"""
    now = time.time()
    with _lock:
        return [
            {"email": email, "job_id": info.get("job_id"), "waited_seconds": int(now - info["since"])}
            for email, info in _waiting.items()
        ]
