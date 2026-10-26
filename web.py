# -*- coding: utf-8 -*-
"""
Muse by Meta 注册机 - WebUI 入口

本地模式（默认）:
    python web.py [--host 127.0.0.1] [--port 8799] [--debug]
    浏览器打开: http://127.0.0.1:8799

服务器模式（云服务器 / Docker 专用）:
    python web.py --server [--port 8799]
    等价于 MUSE_SERVER_MODE=1；默认监听 0.0.0.0，启用登录鉴权，
    状态全部进 MUSE_DATA_DIR。启动前必须配好 MUSE_ADMIN_PASSWORD
    与 MUSE_SESSION_SECRET（见 deploy/.env.server.example），缺失即报错。
"""
import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Muse by Meta 注册机 WebUI")
    parser.add_argument("--host", default=None, help="监听地址（默认：本地 127.0.0.1，--server 时 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--server", action="store_true", help="服务器模式：启用登录鉴权，数据进 MUSE_DATA_DIR")
    args = parser.parse_args()

    if args.server:
        os.environ["MUSE_SERVER_MODE"] = "1"

    from webui import server_auth

    host = args.host
    if host is None:
        host = "0.0.0.0" if server_auth.is_server_mode() else "127.0.0.1"

    from webui.app import run
    run(host=host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
