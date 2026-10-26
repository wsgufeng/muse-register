# 服务器部署（Docker / 云服务器专用）

这是 muse-register 的**服务器模式**：与本地 Windows 用法并行的一套部署方式，
本地模式（`python web.py`，127.0.0.1，无登录）完全不受影响。

## 与本地模式的区别

| | 本地模式 | 服务器模式 |
|---|---|---|
| 启动 | `python web.py` | `docker compose up -d` 或 `python web.py --server` |
| 监听 | 127.0.0.1:8799 | 0.0.0.0:8799（容器内；compose 默认只映射到宿主机回环） |
| 鉴权 | 无 | 登录页（账号密码 + HMAC 会话 cookie，输错 5 次锁 5 分钟） |
| 状态 | 仓库根目录 `muse.sqlite3` + `data/` | `MUSE_DATA_DIR`（容器里是 `/app/data` 卷） |
| 注册驱动 | roxy / cloak（要桌面客户端） | **playwright**（无头 Chromium，无需桌面客户端） |
| Web 服务 | waitress（若已安装） | waitress（镜像内已装） |

## 快速开始

```bash
cd deploy
cp .env.server.example .env.server
# 编辑 .env.server：改掉 MUSE_ADMIN_PASSWORD 与 MUSE_SESSION_SECRET
# 生成密钥：python -c "import secrets; print(secrets.token_hex(24))"
docker compose up -d --build
```

打开 `http://<服务器 IP>:8799`，用 `.env.server` 里的账号登录。
首次登录后在界面里配邮箱池、代理池、卡池，注册驱动默认已是 `playwright`。

健康检查：`curl http://127.0.0.1:8799/api/health` → `{"ok": true}`（无需登录）。

## InstaBox 免费云主机

没有自己的服务器时，可以部署到 [InstaBox](https://instabox.sh) 的免费机
（Debian 13、4 核 8G）。完整流程与一键脚本见
[deploy/instabox/README.md](instabox/README.md)：

```bash
./deploy/instabox/deploy.sh            # 新建一台机器并部署
MACHINE=测试 ./deploy/instabox/deploy.sh  # 复用已有的机器（免费版只有 1 台时）
```

InstaBox 的 `port publish` 自带 HTTPS 终止（网关即反向代理），符合下面
“反向代理 + HTTPS”的要求。注意免费版机器闲置 30 分钟自动暂停、连续运行
满 480 分钟（8 小时）也会强制暂停（随后 10 分钟冷却期无法唤醒），
不适合当 7×24 服务器用；要长期在线请升 Pro。

## 不用 Docker 时

```bash
pip install -r requirements.txt -r requirements-server.txt
python -m playwright install chromium --with-deps
cp deploy/.env.server.example .env.server   # 填好后
MUSE_DATA_DIR=/var/lib/muse-register python web.py --server --port 8799
```

也可以用环境变量代替 `.env.server` 文件（`export MUSE_SERVER_MODE=1` 等），
容器外运行时注意 `MUSE_DATA_DIR` 指向的目录要有写权限。

## 对外访问：必须走反向代理 + HTTPS

应用本身只提供 HTTP 与单管理员登录，**不要直接把 8799 暴露到公网**。
推荐 Caddy（自动签证书）：

```caddyfile
register.example.com {
    reverse_proxy 127.0.0.1:8799
}
```

```bash
caddy run --config /etc/caddy/Caddyfile
```

compose 的端口映射默认就是 `127.0.0.1:8799:8799`，只接受本机连接，
正是为了配合这种部署方式。

## 浏览器：无头行不行？

行，但要认清边界。这是本次新增的 `playwright` 驱动：

- **能跑**：Playwright 自带 Chromium，`--headless` 启动，Linux 服务器/Docker
  里开箱即用；代理、语言/时区仍按出口 IP 匹配；每个任务独立 context，
  与原来 `_run_muse_page_flow` 的流程完全共用（Selenium 风格适配层复用）。
- **缺什么**：原生 Chromium **没有指纹伪装**。RoxyBrowser / CloakBrowser
  是反检测浏览器（改 Canvas/WebGL/字体/硬件指纹等），Playwright 默认
  没有这些——目标站是否放行无头 Chromium，只能实测，不能保证。
- **怎么选**：
  - 服务器上首选 `playwright`（零外部依赖，部署最简单）；
  - 如果目标站拦截无头浏览器，再考虑把 RoxyBrowser 客户端装在另一台
    有桌面的机器上、走 `roxy` 驱动远程连（`ROXY_API_BASE` 改成那台机器的地址），
    或等 `browser_use` / `skyvern` 云端驱动集成；
  - `protocol`（curl_cffi 纯协议）服务器上也能跑，但原项目已注明容易封号。

## 资源需求（实测）

内存主要花在浏览器上，不是应用本身。实测（Playwright 无头 Chromium，
PSS 口径，加载本地页面；真实注册页是 React 应用，每个浏览器再多留 50–100MB）：

- 应用本身（Flask + waitress，空闲）：约 30MB，按 100MB 预算
- 每个并发浏览器：约 200–260MB，预算按 300MB 算
- 操作系统：约 500MB

公式：总内存 ≈ 600MB + 并发数 × 300MB。

| 并发注册数 | 建议内存 | 备注 |
|---|---|---|
| 1 | 1GB（2GB 更稳） | 最小可跑 |
| 2–3 | 2GB | |
| 4（默认线程数） | 4GB | 2GB 能跑但余量小 |
| 8 | 4GB | |
| 16（最大线程数） | 8GB | |

CPU：页面加载时每个浏览器约 0.5–1 核，等验证码时基本空闲；4 核带 8 并发没问题。
磁盘：镜像约 1.5–2GB（含 Chromium），数据卷平时只有几 MB（数据库+日志）。
注意：手动验证码等待期间浏览器一直开着占内存；不要依赖 swap，内存不够时
Chromium 会被系统杀掉、任务直接失败。内存实在紧张时可把注册驱动换成
`protocol`（纯协议、无浏览器，单个任务只要几十 MB，但原项目已注明容易封号）。

## 数据与备份

全部状态都在一个地方：

- Docker：`deploy/data/`（`muse.sqlite3`、日志、`.env`、浏览器画像）
- 备份：停容器后把 `deploy/data/` 整个拷走即可；恢复时拷回同名目录再 `up -d`。

`.env.server`（含管理员密码与会话密钥）**不要**进备份以外的任何地方，
不要提交进仓库（已被 `.gitignore` / `.dockerignore` 排除）。

## 更新

```bash
git pull origin main            # 在 fork 里先合上游更新（见下）
docker compose -f deploy/docker-compose.yml up -d --build
```

上游同步（fork 工作流）：

```bash
git fetch upstream
git merge upstream/main         # 有冲突先在本地解
git push origin main
```

## 排障

| 症状 | 原因 / 处理 |
|---|---|
| 启动即报错 `MUSE_ADMIN_PASSWORD` / `MUSE_SESSION_SECRET` | 服务器模式强制要求这两项，填好 `.env.server` 再启动 |
| 打开页面直接 401 / 跳登录页 | 正常：服务器模式必须先登录；`/api/health` 不需要登录 |
| `/api/fingerprint/health` 里 playwright 显示未安装 | 镜像构建时没装好，重建一次（`--build`）；裸机部署则补 `pip install -r requirements-server.txt` 与 `playwright install chromium --with-deps` |
| 注册任务报 `playwright` 相关 ImportError | 同上，依赖缺失 |
| 浏览器启动报 `Running as root without --no-sandbox` | 驱动已默认加 `--no-sandbox`；若仍报，检查是不是旧镜像没重建 |
| 注册失败、提示风控/验证 | 见“浏览器”一节：先换代理出口重试；仍不行说明目标站识别了无头 Chromium，改走 roxy 或云端驱动 |
| 容器反复重启 | `docker compose logs muse-register` 看报错；常见是 `.env.server` 没填或端口被占 |

## 安全清单（上线前逐项确认）

- [ ] `MUSE_ADMIN_PASSWORD` 与 `MUSE_SESSION_SECRET` 已改成随机强值
- [ ] 8799 只监听 127.0.0.1，对外走带 HTTPS 的反向代理
- [ ] `.env.server` 不在任何公开位置（仓库、镜像层、备份外泄）
- [ ] 定期备份 `deploy/data/`，恢复流程演练过一次
