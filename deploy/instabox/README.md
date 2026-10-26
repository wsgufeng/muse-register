# InstaBox 部署

把 muse-register（服务器模式）部署到一台 [InstaBox](https://instabox.sh) 云主机上。
机器是 Debian 13、4 核 8G，装好后打开公网地址就能登录使用。

## 前提

- 一个 InstaBox 账号（免费版就行：1 台机器、4 核 8G）
- 本机装好 `instabox` CLI 并登录：
  ```bash
  npx instabox@latest   # 按提示用设备码登录，并装好 CLI
  instabox ls           # 看到你的机器就说明通了
  ```

## 一键部署

在仓库根目录执行：

```bash
./deploy/instabox/deploy.sh
```

它会依次做：没有机器就新建一台 `muse-register` → 暂停/关机就恢复 →
上传安装脚本与配置 → 机器内安装（系统包、依赖、Chromium）→
以 systemd 服务启动 → 公开 8799 端口，最后打印公网地址。

首次执行时会自动生成登录配置（账号 `admin`、随机密码与会话密钥），
密码只在终端显示一次，请记好；配置文件保存在
`deploy/instabox/.env.server`（权限 600，不要提交进仓库）。

免费版只有 1 台机器名额，已经有一台（比如叫 `测试`）时，复用它：

```bash
MACHINE=测试 ./deploy/instabox/deploy.sh
```

## 手动部署（不用 deploy.sh 时）

```bash
# 1. 准备机器（没有就新建；暂停了就恢复）
instabox create --name muse-register
instabox resume muse-register   # 仅暂停时需要

# 2. 准备配置：按 .env.server.example 填好，放到机器 home 里
cp deploy/instabox/.env.server.example /tmp/.env.server
# 编辑 /tmp/.env.server：填 MUSE_ADMIN_PASSWORD（≥8 位）与 MUSE_SESSION_SECRET（≥16 位）
instabox cp /tmp/.env.server muse-register:/data/home/.env.server

# 3. 上传安装脚本并执行
instabox cp deploy/instabox/setup.sh muse-register:/data/home/setup-muse-register.sh
instabox exec --stream muse-register -- bash /data/home/setup-muse-register.sh

# 4. 公开端口，拿到公网地址
instabox port publish muse-register 8799
```

## 部署后

- 打开公网地址，看到登录页，用 `admin` 与配置文件里的密码登录。
- 默认注册驱动是 `playwright`（无头 Chromium），在 WebUI 里选邮箱来源、
  导入邮箱池/卡池、填代理池，就可以开始注册任务。
- 本机访问（不公开端口时）：`instabox port-forward muse-register 8799`，
  然后打开 http://127.0.0.1:8799。

## 日常操作

```bash
# 更新到最新代码（重跑一键脚本即可，配置与数据不动）
./deploy/instabox/deploy.sh

# 看日志
instabox exec muse-register -- journalctl -u muse-register -f

# 重启服务
instabox exec muse-register -- sudo systemctl restart muse-register

# 停机 / 恢复（停机不计费、不丢数据；恢复后服务自启）
instabox stop muse-register
instabox start muse-register
```

## 机器里的布局

| 位置 | 内容 |
|---|---|
| `/data/home/muse-register` | 代码 + 虚拟环境 `venv/` |
| `/data/home/muse-register-data` | 数据库、`.env.server`、日志（`MUSE_DATA_DIR`） |
| `/etc/systemd/system/muse-register.service` | 开机自启的服务 |

数据放在持久盘 `/data` 上：机器重启、暂停恢复都不会丢。根盘只有 16GB，
Chromium 约占 400MB，留意别把根盘塞满。

## 限制与注意

- **免费版会定时暂停**：闲置 30 分钟自动暂停；连续运行满 480 分钟
  （8 小时）也会强制暂停，且随后有 10 分钟冷却期无法唤醒。暂停后公网
  地址打不开，执行 `instabox resume muse-register` 恢复（服务会自启）。
  这机器不适合当 7×24 服务器用；要长期在线请升 Pro（可关自动暂停、
  无最长运行限制）。
- **内存**：应用本身约 30MB，每个并发浏览器约 200–260MB。
  总内存 ≈ 600MB + 并发数 × 300MB（详见 `deploy/README.md` 的实测表）。
  这台 8GB 机器跑 8 并发很宽松，拉满 16 也够。
- **不要依赖 swap**：内存不够时系统会杀 Chromium，任务直接失败。
- **登录密码只存一份**：在机器的 `muse-register-data/.env.server`
  （600 权限）与你本机的 `deploy/instabox/.env.server`；丢了就改配置
  里的密码并重启服务。
- 机器暂停/关机期间 SSH 与 exec 都会断开，正在跑的注册任务会中断，
  恢复后需在 WebUI 里看任务状态、按需重跑。

## 排障

| 现象 | 处理 |
|---|---|
| `deploy.sh` 报找不到 `instabox` | 先装 CLI：`npx instabox@latest` |
| `instabox ls` 失败 | 先 `instabox login` 登录，或设置 `INSTABOX_API_KEY` |
| 建机器失败（免费版） | 已有 1 台机器时不能再建，用 `MACHINE=<已有机器名>` 复用 |
| setup.sh 报 git clone 失败 | 机器访问不了 github.com，等网络恢复后重跑（脚本幂等） |
| setup.sh 报 Chromium 安装失败 | 多半是 apt 源问题，重跑一次；仍失败则进机器手动执行 `python -m playwright install chromium --with-deps` |
| 服务 60 秒未就绪 | `instabox exec <机器> -- journalctl -u muse-register -n 100` 看报错；常见是密码/密钥长度不够 |
| 登录页打不开 | 先确认机器在运行（`instabox ls`），再确认端口已公开（`instabox port publish` 重新执行一次） |
| 打开页面是登录页但密码不对 | 对照本机 `deploy/instabox/.env.server` 里的密码；改了机器上的配置要重启服务才生效 |
