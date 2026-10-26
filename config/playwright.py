# -*- coding: utf-8 -*-
"""Playwright 无头 Chromium 注册配置（服务器模式默认驱动）。

与 CloakBrowser 的区别：这是系统里自带的 Chromium，不依赖任何
第三方指纹浏览器客户端/二进制，适合在 Linux 云服务器上无头运行。
代价是没有指纹伪装——是否会被目标站识别为自动化，只能实测。
"""
from config.env_loader import apply_env_overrides

# 是否无头启动：服务器上必须 True；本地调试可设 False 看窗口。
PLAYWRIGHT_HEADLESS: bool = True

# 显式指定 Chromium 可执行文件；留空则用 playwright 自带的 Chromium。
PLAYWRIGHT_EXECUTABLE_PATH: str = ""

# 持久化用户目录；留空则每次用临时上下文（不保留 Cookie/缓存）。
# 例："/app/data/playwright-profiles/default"
PLAYWRIGHT_USER_DATA_DIR: str = ""

# 显式指定语言/时区；留空则在 PLAYWRIGHT_GEOIP=True 时按出口 IP 自动推断。
# 例如：PLAYWRIGHT_LOCALE="ja-JP"，PLAYWRIGHT_TIMEZONE="Asia/Tokyo"。
PLAYWRIGHT_LOCALE: str = ""
PLAYWRIGHT_TIMEZONE: str = ""

# 使用当前出口 IP 自动匹配时区/语言。
PLAYWRIGHT_GEOIP: bool = True

# 是否把本项目传入/代理池抽取的代理传给 Chromium。
PLAYWRIGHT_USE_PROXY: bool = True

# 额外 Chromium 启动参数，例如 ["--window-size=1280,800"]。
PLAYWRIGHT_EXTRA_ARGS: list = []

# 页面加载超时（秒），与 Cloak 共用同一套流程。
PLAYWRIGHT_TIMEOUT: int = 90

# 调试时保留浏览器不自动关闭。
PLAYWRIGHT_KEEP_BROWSER_OPEN: bool = False

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {'PLAYWRIGHT_HEADLESS': 'bool', 'PLAYWRIGHT_EXECUTABLE_PATH': 'str', 'PLAYWRIGHT_USER_DATA_DIR': 'str', 'PLAYWRIGHT_LOCALE': 'str', 'PLAYWRIGHT_TIMEZONE': 'str', 'PLAYWRIGHT_GEOIP': 'bool', 'PLAYWRIGHT_USE_PROXY': 'bool', 'PLAYWRIGHT_EXTRA_ARGS': 'list_str_multiline', 'PLAYWRIGHT_TIMEOUT': 'int', 'PLAYWRIGHT_KEEP_BROWSER_OPEN': 'bool'})
