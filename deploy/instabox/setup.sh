#!/usr/bin/env bash
# muse-register — InstaBox 机器内安装脚本
#
# 用途：在一台 InstaBox 机器（Debian 13）里安装并启动服务器模式。
# 幂等：重复执行会拉取最新代码、重装依赖、重启服务，不会丢数据。
#
# 正常用法（由 deploy/instabox/deploy.sh 驱动，不要手打）：
#   instabox cp setup.sh <machine>:/data/home/setup-muse-register.sh
#   instabox exec --stream <machine> -- bash /data/home/setup-muse-register.sh
#
# 手动用法（已在机器里）：
#   bash setup.sh
#
# 环境变量（全部可选）：
#   REPO_URL        仓库地址（默认 https://github.com/wsgufeng/muse-register.git）
#   REPO_BRANCH     分支（默认 main）
#   INSTALL_DIR     安装目录（默认 /data/home/muse-register）
#   DATA_DIR        状态目录（默认 /data/home/muse-register-data，持久盘上）
#   PORT            监听端口（默认 8799）
#   MUSE_ADMIN_PASSWORD / MUSE_SESSION_SECRET
#                   仅在没有任何 .env.server 时用它们生成配置
#   SKIP_APT=1              跳过 apt 安装（测试用）
#   SKIP_PLAYWRIGHT_INSTALL=1  跳过 Chromium 安装（测试用；会失去 playwright 驱动）
#   NO_SYSTEMD=1            不装 systemd 服务，改用 nohup 启动（测试用）
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/wsgufeng/muse-register.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/data/home/muse-register}"
DATA_DIR="${DATA_DIR:-/data/home/muse-register-data}"
PORT="${PORT:-8799}"
SERVICE_NAME="muse-register"

log()  { echo "[setup] $*"; }
warn() { echo "[setup] 警告：$*" >&2; }
fail() { echo "[setup] 错误：$*" >&2; exit 1; }

# ---- 0. 提权方式：root / sudo / 无 ----
SUDO=""
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    SUDO="sudo"
else
    warn "当前用户不是 root 且无免密 sudo，apt 与 systemd 步骤可能失败"
    warn "如需系统包/服务，请用有 sudo 的账号重跑"
fi

# ---- 1. 系统包 ----
if [ "${SKIP_APT:-0}" = "1" ]; then
    log "跳过 apt（SKIP_APT=1）"
else
    if [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
        log "安装系统包 git / python3-venv / curl ..."
        $SUDO apt-get update -qq
        $SUDO apt-get install -y -qq git python3 python3-venv python3-pip curl ca-certificates \
            || fail "apt 安装失败，请检查网络与权限"
    else
        warn "无提权方式，跳过 apt；假定 git 与 python3-venv 已装好"
    fi
fi
command -v git >/dev/null || fail "找不到 git，请先装好再重跑"
command -v python3 >/dev/null || fail "找不到 python3，请先装好再重跑"
python3 -c "import venv" 2>/dev/null || fail "python3-venv 未安装（需 apt install python3-venv）"

# ---- 2. 拉代码（已有仓库则 fast-forward 更新；有本地改动会直接报错，不覆盖） ----
if [ -d "$INSTALL_DIR/.git" ]; then
    log "更新已有仓库 $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --quiet origin \
        || fail "git fetch 失败，请检查机器能否访问 github.com"
    git -C "$INSTALL_DIR" checkout --quiet "$REPO_BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only --quiet origin "$REPO_BRANCH" \
        || fail "git pull 失败：机器上的仓库有本地改动，请先处理（提交或删除改动）再重跑"
else
    log "克隆仓库到 $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR" \
        || fail "git clone 失败，请检查机器能否访问 github.com"
fi
log "代码版本：$(git -C "$INSTALL_DIR" rev-parse --short HEAD)（$(git -C "$INSTALL_DIR" log -1 --format=%ci)）"

# ---- 3. 虚拟环境 + 依赖 ----
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    log "创建虚拟环境"
    python3 -m venv "$INSTALL_DIR/venv" || fail "创建 venv 失败"
fi
log "安装 Python 依赖（requirements.txt + requirements-server.txt）"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet \
    -r "$INSTALL_DIR/requirements.txt" \
    -r "$INSTALL_DIR/requirements-server.txt" \
    || fail "pip 安装依赖失败"

# ---- 4. Playwright 浏览器（playwright 驱动必需） ----
if [ "${SKIP_PLAYWRIGHT_INSTALL:-0}" = "1" ]; then
    warn "跳过 Chromium 安装（SKIP_PLAYWRIGHT_INSTALL=1），playwright 驱动将不可用"
else
    log "安装 Playwright Chromium（含系统依赖）"
    if [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
        $SUDO "$INSTALL_DIR/venv/bin/python" -m playwright install chromium --with-deps \
            || fail "Chromium 安装失败"
    else
        "$INSTALL_DIR/venv/bin/python" -m playwright install chromium \
            || fail "Chromium 安装失败（无 sudo 装不上系统依赖，请用有 sudo 的账号重跑）"
    fi
fi

# ---- 5. 配置：$DATA_DIR/.env.server 是唯一事实来源 ----
mkdir -p "$DATA_DIR"
ENV_FILE="$DATA_DIR/.env.server"
if [ -f "$ENV_FILE" ]; then
    log "使用已有配置 $ENV_FILE（密码等保持不变）"
elif [ -f "$HOME/.env.server" ]; then
    log "把 $HOME/.env.server 移入 $ENV_FILE"
    mv "$HOME/.env.server" "$ENV_FILE"
elif [ -n "${MUSE_ADMIN_PASSWORD:-}" ] && [ -n "${MUSE_SESSION_SECRET:-}" ]; then
    log "按环境变量从示例生成配置"
    cp "$INSTALL_DIR/deploy/.env.server.example" "$ENV_FILE"
    # sed 替换值前转义 & | \，避免密码含特殊字符时写坏
    esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
    sed -i "s|^MUSE_DATA_DIR=.*|MUSE_DATA_DIR=$(esc "$DATA_DIR")|" "$ENV_FILE"
    sed -i "s|^MUSE_ADMIN_PASSWORD=.*|MUSE_ADMIN_PASSWORD=$(esc "$MUSE_ADMIN_PASSWORD")|" "$ENV_FILE"
    sed -i "s|^MUSE_SESSION_SECRET=.*|MUSE_SESSION_SECRET=$(esc "$MUSE_SESSION_SECRET")|" "$ENV_FILE"
else
    fail "没有配置：请先把填好的 .env.server 放到 $HOME/.env.server，或设置 MUSE_ADMIN_PASSWORD 与 MUSE_SESSION_SECRET 再重跑（见 deploy/instabox/README.md）"
fi
chmod 600 "$ENV_FILE"

# 校验长度（应用启动时也会校验，这里先报清楚的错）
ADMIN_PW="$(grep '^MUSE_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
SESSION_SECRET="$(grep '^MUSE_SESSION_SECRET=' "$ENV_FILE" | cut -d= -f2-)"
[ "${#ADMIN_PW}" -ge 8 ] || fail "MUSE_ADMIN_PASSWORD 太短（至少 8 位），请改 $ENV_FILE 后重跑"
[ "${#SESSION_SECRET}" -ge 16 ] || fail "MUSE_SESSION_SECRET 太短（至少 16 位），请改 $ENV_FILE 后重跑"

# ---- 6. 启动：优先 systemd，否则 nohup ----
RUN_USER="$(id -un)"
USE_SYSTEMD=0
if [ "${NO_SYSTEMD:-0}" != "1" ] && [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ] \
    && { [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; }; then
    USE_SYSTEMD=1
fi

if [ "$USE_SYSTEMD" = "1" ]; then
    log "安装 systemd 服务 $SERVICE_NAME（用户 $RUN_USER）"
    $SUDO tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=Muse Register (server mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/python web.py --server --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --quiet "$SERVICE_NAME"
    $SUDO systemctl restart "$SERVICE_NAME"
    log "服务已启动：journalctl -u $SERVICE_NAME -f  查看日志"
else
    if [ "${NO_SYSTEMD:-0}" != "1" ]; then
        warn "未检测到可用的 systemd（或无权写 /etc/systemd/system），改用 nohup 启动"
        warn "机器重启后需手动重跑本脚本；如需开机自启请自行配 systemd 或 cron @reboot"
    fi
    cat > "$INSTALL_DIR/run.sh" <<EOF
#!/usr/bin/env bash
# muse-register 启动器（setup.sh 生成；不要手改，改 $ENV_FILE 后重启即可）
set -a
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
set +a
export MUSE_DATA_DIR="$DATA_DIR"
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/web.py" --server --port $PORT
EOF
    chmod +x "$INSTALL_DIR/run.sh"
    if [ -f "$DATA_DIR/server.pid" ] && kill -0 "$(cat "$DATA_DIR/server.pid")" 2>/dev/null; then
        log "停止旧进程 $(cat "$DATA_DIR/server.pid")"
        kill "$(cat "$DATA_DIR/server.pid")" 2>/dev/null || true
        sleep 2
    fi
    log "nohup 启动服务，日志：$DATA_DIR/server.log"
    nohup "$INSTALL_DIR/run.sh" > "$DATA_DIR/server.log" 2>&1 &
    echo $! > "$DATA_DIR/server.pid"
fi

# ---- 7. 验活：等 /api/health 变 200 ----
log "等待服务就绪（最多约 60 秒）..."
OK=0
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null; then
        OK=1
        break
    fi
    sleep 2
done
[ "$OK" = "1" ] || fail "服务 60 秒内未就绪，请看日志排障（systemd：journalctl -u $SERVICE_NAME -n 100；nohup：tail -n 100 $DATA_DIR/server.log）"

echo
log "部署完成"
log "本地地址：http://127.0.0.1:$PORT（机器内）"
log "登录账号：$(grep '^MUSE_ADMIN_USER=' "$ENV_FILE" | cut -d= -f2-)，密码见 $ENV_FILE"
log "公网地址：本机执行 instabox port publish <机器名> $PORT"
log "数据目录：$DATA_DIR（在持久盘 /data 上，机器重启/重建快照都不丢）"
log "更新：重跑本脚本即可（git pull + 重装依赖 + 重启服务）"
