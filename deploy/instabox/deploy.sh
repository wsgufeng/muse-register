#!/usr/bin/env bash
# muse-register — InstaBox 一键部署（在本机执行）
#
# 作用：把服务器模式部署到一台 InstaBox 机器上，并给出公网访问地址。
# 流程：检查 CLI 与登录 → 准备机器（没有就新建，暂停/关机就恢复）
#       → 传 setup.sh 与 .env.server → 机器内执行安装 → 公开 8799 端口。
# 幂等：重复执行会更新代码并重启服务，登录密码等配置保持不变。
#
# 用法：
#   ./deploy/instabox/deploy.sh                  # 新建一台 muse-register 机器并部署
#   MACHINE=测试 ./deploy/instabox/deploy.sh      # 部署到已有的机器上（免费版只有 1 台机器时用这个）
#   ENV_FILE=./my.env ./deploy/instabox/deploy.sh  # 用指定的配置文件
#
# 配置文件（默认 deploy/instabox/.env.server）：
#   首次执行时自动生成：账号 admin、随机密码与会话密钥，只显示一次，请记好。
#   也可以提前写好，或用环境变量 MUSE_ADMIN_PASSWORD / MUSE_SESSION_SECRET 传入。
#   生成后文件权限 600，只在你本机保存一份，不要提交进仓库。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACHINE="${MACHINE:-muse-register}"
PORT="${PORT:-8799}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env.server}"
EXAMPLE="$SCRIPT_DIR/.env.server.example"
SETUP_SRC="$SCRIPT_DIR/setup.sh"
REMOTE_SETUP="/data/home/setup-muse-register.sh"
REMOTE_ENV="/data/home/.env.server"
DATA_DIR="/data/home/muse-register-data"

log()  { echo "[deploy] $*"; }
warn() { echo "[deploy] 警告：$*" >&2; }
fail() { echo "[deploy] 错误：$*" >&2; exit 1; }

# ---- 0. 前置检查 ----
command -v instabox >/dev/null 2>&1 \
    || fail "找不到 instabox CLI。先安装：npx instabox@latest（会引导登录并装好 CLI），或 curl -fsSL https://api.instabox.sh/install.sh | sh"
[ -f "$SETUP_SRC" ] || fail "找不到 $SETUP_SRC，请在仓库根目录执行本脚本"
[ -f "$EXAMPLE" ] || fail "找不到 $EXAMPLE"
instabox ls >/dev/null 2>&1 \
    || fail "instabox 未登录或 API 不通。请先执行 instabox login（设备码登录），或设置 INSTABOX_API_KEY"

rand_hex() { # 生成 n 字节随机 hex；优先 openssl，否则 python3
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$1"
    else
        python3 -c "import secrets,sys; print(secrets.token_hex($1))"
    fi
}

# ---- 1. 配置文件：没有就生成（只生成一次，之后一直复用） ----
GENERATED=0
if [ ! -f "$ENV_FILE" ]; then
    ADMIN_PW="${MUSE_ADMIN_PASSWORD:-$(rand_hex 12)}"   # 24 位 hex，≥8 位
    SESSION_SECRET="${MUSE_SESSION_SECRET:-$(rand_hex 24)}"  # 48 位 hex，≥16 位
    [ -n "${MUSE_ADMIN_PASSWORD:-}" ] || GENERATED=1
    cp "$EXAMPLE" "$ENV_FILE"
    esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
    sed -i "s|^MUSE_DATA_DIR=.*|MUSE_DATA_DIR=$(esc "$DATA_DIR")|" "$ENV_FILE"
    sed -i "s|^MUSE_ADMIN_PASSWORD=.*|MUSE_ADMIN_PASSWORD=$(esc "$ADMIN_PW")|" "$ENV_FILE"
    sed -i "s|^MUSE_SESSION_SECRET=.*|MUSE_SESSION_SECRET=$(esc "$SESSION_SECRET")|" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log "已生成配置文件 $ENV_FILE（权限 600）"
    if [ "$GENERATED" = "1" ]; then
        echo
        echo "==================== 只显示一次，请记好 ===================="
        echo "  登录账号：admin"
        echo "  登录密码：$ADMIN_PW"
        echo "  配置文件：$ENV_FILE"
        echo "============================================================"
        echo
    fi
else
    log "使用已有配置文件 $ENV_FILE（密码保持不变）"
fi

# ---- 2. 机器：没有就新建，暂停/关机就恢复，等它 running ----
# 状态字段叫 state（不是 status）；暂停态叫 suspended。机器不存在时 get 会报错。
machine_state() {
    instabox get "$MACHINE" --json 2>/dev/null \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('state') or d.get('status',''))" 2>/dev/null \
        || echo ""
}
STATE="$(machine_state)"
if [ -z "$STATE" ]; then
    # API 要求机器名：1–63 位小写字母/数字/横线，字母或数字开头。
    # 控制台建的中文名（如“测试”）只能复用，不能用 CLI 新建。
    if [[ ! "$MACHINE" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]; then
        fail "机器 $MACHINE 不存在，且名字不符合新建规则（小写字母/数字/横线，1–63 位）。已有机器请用 MACHINE=<已有机器名> 复用，或换个合规名字新建"
    fi
    log "新建机器 $MACHINE（默认模板 agent-browser；免费版只能建 1 台）"
    instabox create --name "$MACHINE" \
        || fail "建机器失败。免费版只有 1 台机器名额——已有机器时请用 MACHINE=<已有机器名> 复用它"
    STATE="creating"
fi
case "$STATE" in
    suspended) log "机器处于暂停，恢复中..."; instabox resume "$MACHINE" ;;
    stopped)   log "机器处于关机，启动中..."; instabox start "$MACHINE" ;;
    running)   log "机器 $MACHINE 已在运行" ;;
    creating|starting|resuming) log "机器当前状态：$STATE，等待就绪..." ;;
    *)         fail "机器当前状态：$STATE，无法部署。请到控制台看一眼再重跑" ;;
esac
log "等待机器 running（最多约 120 秒）..."
OK=0
for _ in $(seq 1 24); do
    if [ "$(machine_state)" = "running" ]; then OK=1; break; fi
    sleep 5
done
[ "$OK" = "1" ] || fail "机器 120 秒内未进入 running，请到控制台看一眼状态再重跑"

# ---- 3. 传文件：setup.sh 与 .env.server（.env.server 只在机器上还没有时用） ----
log "上传安装脚本与配置"
instabox cp "$SETUP_SRC" "$MACHINE:$REMOTE_SETUP" \
    || fail "上传 setup.sh 失败"
instabox cp "$ENV_FILE" "$MACHINE:$REMOTE_ENV" \
    || fail "上传 .env.server 失败"

# ---- 4. 机器内执行安装（--stream 实时看输出；setup.sh 幂等，中断可重跑） ----
log "在机器内执行安装（apt + 依赖 + Chromium，约几分钟）..."
instabox exec --stream --timeout 900 "$MACHINE" -- bash "$REMOTE_SETUP" \
    || fail "安装失败。setup.sh 是幂等的：修好报错原因后重跑本脚本即可"

# ---- 5. 公开端口 ----
log "公开端口 $PORT ..."
PUBLIC_URL="$(instabox port publish "$MACHINE" "$PORT" 2>&1)" \
    || fail "公开端口失败（可稍后手动执行 instabox port publish $MACHINE $PORT）"

echo
log "部署完成"
log "公网地址：$PUBLIC_URL"
log "登录账号：admin（密码见 $ENV_FILE，只在首次生成时显示过）"
log "本机访问：instabox port-forward $MACHINE $PORT，然后打开 http://127.0.0.1:$PORT"
log "看日志：instabox exec $MACHINE -- journalctl -u muse-register -f"
log "更新：重跑本脚本即可"
echo
warn "免费版注意：机器闲置 30 分钟会自动暂停，连续运行满 480 分钟（8 小时）"
warn "也会强制暂停，且随后有 10 分钟冷却期无法唤醒；暂停后公网地址打不开，"
warn "需要时执行 instabox resume $MACHINE 恢复。这机器不适合 7×24 小时跑，"
warn "要长期在线请升 Pro（可关自动暂停、无最长运行限制）。"
