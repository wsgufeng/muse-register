# -*- coding: utf-8 -*-
"""Playwright 无头 Chromium 驱动（服务器模式）。

思路：项目里所有浏览器注册流程最终都跑在 `_run_muse_page_flow` 上，
它只要求一个 Selenium 风格的 driver 对象。CloakBrowser 那条路已经证明
Playwright 的 page 可以包进 `CloakSeleniumDriver` 直接复用——这里只是把
`cloakbrowser` 第三方包换成系统自带的 Playwright Chromium，启动方式改成
无头，其余流程一行不用改。

适用：Linux 云服务器 / Docker（无显示器、无桌面客户端）。
不适用：需要指纹伪装的场景——原生 Chromium 没有 Roxy/Cloak 那种
指纹 spoof，目标站是否放行只能实测；代理、语言/时区仍按出口匹配。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import playwright as _cfg
from core.cloakbrowser_driver import CloakSeleniumDriver

logger = logging.getLogger(__name__)


@dataclass
class PlaywrightOpenResult:
    profile_id: str = "playwright"
    raw: dict | None = None


def _build_playwright_locale_options(proxy_url: str | None = None) -> dict:
    """生成 Playwright 的语言/时区配置（复用 Cloak 的出口 IP 地理推断）。"""
    explicit_locale = str(getattr(_cfg, "PLAYWRIGHT_LOCALE", "") or "").strip()
    explicit_timezone = str(getattr(_cfg, "PLAYWRIGHT_TIMEZONE", "") or "").strip()
    out: dict[str, str] = {}
    if explicit_locale:
        out["locale"] = explicit_locale
        out["accept_language"] = f"{explicit_locale},{explicit_locale.split('-')[0]};q=0.9,en-US;q=0.8,en;q=0.7"
    if explicit_timezone:
        out["timezone"] = explicit_timezone
    if explicit_locale and explicit_timezone:
        return out
    if not bool(getattr(_cfg, "PLAYWRIGHT_GEOIP", True)):
        return out
    try:
        from core.cloakbrowser_driver import _detect_cloak_exit_geo
        from config.browser import build_browser_environment

        geo = _detect_cloak_exit_geo(proxy_url)
        profile = build_browser_environment(geo)
        out.setdefault("locale", str(profile.get("navigator_language") or ""))
        out.setdefault("timezone", str(profile.get("timezone_iana") or ""))
        out.setdefault("accept_language", str(profile.get("accept_language") or ""))
    except Exception as exc:
        logger.debug("[Playwright] 构建自动语言/时区失败：%s: %s", type(exc).__name__, exc)
    return {k: v for k, v in out.items() if v}


class PlaywrightSeleniumDriver(CloakSeleniumDriver):
    """继承 CloakSeleniumDriver 的全部页面操作，只多管 Playwright 生命周期。

    quit() 时除了关 context/browser，还要停掉 sync_playwright 实例，
    否则 Chromium 进程会残留。
    """

    def __init__(self, browser: Any, context: Any | None, page: Any, pw: Any, proxy_relay: Any | None = None):
        super().__init__(browser=browser, context=context, page=page, proxy_relay=proxy_relay)
        self._pw = pw
        self._page_load_timeout_ms = int(getattr(_cfg, "PLAYWRIGHT_TIMEOUT", 90) or 90) * 1000
        self._registration_log_prefix = "[Playwright注册]"

    def quit(self) -> None:
        try:
            super().quit()
        finally:
            try:
                if getattr(self, "_pw", None) is not None:
                    self._pw.stop()
            except Exception:
                pass


def build_playwright_driver(proxy: str | None = None) -> tuple[PlaywrightSeleniumDriver, PlaywrightOpenResult]:
    """启动 Playwright 无头 Chromium 并返回 Selenium 风格 driver。

    proxy=None  时按 config.proxy.PROXY_POOL 随机抽取；
    proxy=""    时显式禁用代理；
    proxy="..." 时使用指定代理。
    """
    if proxy is None and bool(getattr(_cfg, "PLAYWRIGHT_USE_PROXY", True)):
        try:
            from config.proxy import pick_proxy
        except Exception:
            proxy = None
        else:
            proxy = str(pick_proxy() or "").strip() or None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("未安装 playwright，请执行：pip install -r requirements-server.txt") from exc

    launch_args = [
        "--no-sandbox",  # 容器/root 下必需
        "--disable-dev-shm-usage",  # /dev/shm 小的容器会崩溃，改走 /tmp
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-gpu",
    ]
    _extra = getattr(_cfg, "PLAYWRIGHT_EXTRA_ARGS", []) or []
    if isinstance(_extra, str):  # 容错：环境变量没走 list 解析时按空白拆分
        _extra = _extra.split()
    launch_args.extend(list(_extra))

    locale_opts = _build_playwright_locale_options(proxy)
    # Playwright 更可靠的写法是把认证信息拆成 username/password 单独传，
    # 而不是嵌在 server URL 里（部分代理实现不认 URL 内嵌凭证）。
    proxy_arg = None
    if proxy:
        from urllib.parse import urlparse

        parts = urlparse(proxy)
        server = f"{parts.scheme or 'http'}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
        proxy_arg = {"server": server}
        if parts.username:
            proxy_arg["username"] = parts.username
        if parts.password:
            proxy_arg["password"] = parts.password

    logger.info(
        "[Playwright] 启动 Chromium：headless=%s proxy=%s locale=%s timezone=%s persistent=%s",
        bool(getattr(_cfg, "PLAYWRIGHT_HEADLESS", True)),
        proxy or "无",
        locale_opts.get("locale") or "自动/默认",
        locale_opts.get("timezone") or "自动/默认",
        bool(str(getattr(_cfg, "PLAYWRIGHT_USER_DATA_DIR", "") or "").strip()),
    )

    pw = sync_playwright().start()
    try:
        executable_path = str(getattr(_cfg, "PLAYWRIGHT_EXECUTABLE_PATH", "") or "").strip() or None
        user_data_dir = str(getattr(_cfg, "PLAYWRIGHT_USER_DATA_DIR", "") or "").strip()
        context_kwargs: dict[str, Any] = {}
        if locale_opts.get("locale"):
            context_kwargs["locale"] = locale_opts["locale"]
        if locale_opts.get("timezone"):
            context_kwargs["timezone_id"] = locale_opts["timezone"]
        if locale_opts.get("accept_language"):
            context_kwargs["extra_http_headers"] = {"Accept-Language": locale_opts["accept_language"]}
        if proxy_arg:
            context_kwargs["proxy"] = proxy_arg

        launch_kwargs: dict[str, Any] = {
            "headless": bool(getattr(_cfg, "PLAYWRIGHT_HEADLESS", True)),
            "args": launch_args,
        }
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        if user_data_dir:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(user_data_dir, **launch_kwargs, **context_kwargs)
            page = context.new_page()
            browser = getattr(context, "browser", None) or context
        else:
            browser = pw.chromium.launch(**launch_kwargs)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
    except Exception:
        try:
            pw.stop()
        except Exception:
            pass
        raise

    driver = PlaywrightSeleniumDriver(browser=browser, context=context, page=page, pw=pw)
    driver.set_page_load_timeout(int(getattr(_cfg, "PLAYWRIGHT_TIMEOUT", 90) or 90))
    return driver, PlaywrightOpenResult(raw={
        "driver": "playwright",
        "headless": bool(getattr(_cfg, "PLAYWRIGHT_HEADLESS", True)),
        "proxy": proxy or "",
        "locale": locale_opts.get("locale") or "",
        "timezone": locale_opts.get("timezone") or "",
    })
