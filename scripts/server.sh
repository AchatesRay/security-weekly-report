#!/bin/bash
# 安全管理后台服务器管理脚本
# 用法: ./server.sh {start|stop|restart|status} [port]
#
# 2026-09-29 修复：
#   1. stop 原先只靠 `ss -tlnp` 找 PID。该命令在非 root 下看不到他人进程的
#      pid=，且系统没装 iproute2 时整条管道为空，脚本会静默报告“未运行”而
#      实际服务仍在跑。现在改为：优先用 PID 文件，并校验该 PID 确实是
#      config_server.py，找不到时再退回 ss；停不掉会明确报错并返回非 0。
#   2. status 原先会把 /api/config/settings 的响应直接打印到终端（该接口会
#      回传配置内容），现改为只报告可达性。
#   3. 优先使用项目 venv 的解释器，避免用系统 python3 缺依赖起不来。

set -u

cd "$(dirname "$0")/.." || exit 1

PORT=${2:-8090}
PID_FILE="/tmp/config_server_${PORT}.pid"
LOG_FILE="/tmp/config_server_${PORT}.log"
SERVER_SCRIPT="server/config_server.py"

# 选择解释器：优先项目 venv
if [ -x "venv/bin/python3" ]; then
  PYTHON="venv/bin/python3"
elif [ -x "venv/bin/python" ]; then
  PYTHON="venv/bin/python"
else
  PYTHON="python3"
fi

# 校验 PID 是否是我们这个服务
is_our_server() {
  pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "config_server.py" || return 1
  fi
  return 0
}

# 通过端口反查 PID（需要权限，可能为空）
port_pid() {
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp "sport = :${PORT}" 2>/dev/null | grep -oP 'pid=\K\d+' | head -1
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | head -1
  fi
}

# 综合判断当前运行中的 PID
current_pid() {
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if is_our_server "$pid"; then
      echo "$pid"
      return
    fi
  fi
  pid=$(port_pid)
  if is_our_server "$pid"; then
    echo "$pid"
  fi
}

case "${1:-status}" in
  start)
    existing=$(current_pid)
    if [ -n "$existing" ]; then
      echo "[SERVER] 已在运行 PID: $existing (端口 $PORT)"
      echo "$existing" > "$PID_FILE"
      exit 0
    fi
    rm -f "$PID_FILE"
    nohup "$PYTHON" "$SERVER_SCRIPT" "$PORT" > "$LOG_FILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PID_FILE"
    sleep 1
    if is_our_server "$PID"; then
      echo "[SERVER] 已启动 PID: $PID (端口 $PORT, 解释器 $PYTHON)"
    else
      echo "[SERVER] 启动失败，请查看日志: $LOG_FILE"
      tail -n 20 "$LOG_FILE" 2>/dev/null
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;

  stop)
    found=$(current_pid)
    if [ -z "$found" ]; then
      echo "[SERVER] 未运行"
      rm -f "$PID_FILE"
      exit 0
    fi
    kill "$found" 2>/dev/null
    for _ in 1 2 3 4 5; do
      sleep 1
      is_our_server "$found" || break
    done
    if is_our_server "$found"; then
      echo "[SERVER] 进程 $found 未在 5 秒内退出，发送 SIGKILL"
      kill -9 "$found" 2>/dev/null
      sleep 1
    fi
    if is_our_server "$found"; then
      echo "[SERVER] 停止失败，进程仍在运行: $found" >&2
      exit 1
    fi
    echo "[SERVER] 已停止 PID: $found"
    rm -f "$PID_FILE"
    ;;

  restart)
    "$0" stop "$PORT"
    sleep 1
    "$0" start "$PORT"
    ;;

  status)
    found=$(current_pid)
    if [ -n "$found" ]; then
      echo "[SERVER] 运行中 PID: $found (端口 $PORT)"
      echo "$found" > "$PID_FILE"
      # 只探测可达性，不打印接口返回的配置内容
      if command -v curl >/dev/null 2>&1; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
               "http://localhost:$PORT/server/config.html" || echo "000")
        case "$code" in
          200) echo "[SERVER] 管理页面可访问 (HTTP $code)" ;;
          401) echo "[SERVER] 管理页面要求认证 (HTTP 401)" ;;
          000) echo "[SERVER] 端口监听中但未能建立连接" ;;
          *)   echo "[SERVER] 管理页面返回 HTTP $code" ;;
        esac
      fi
    else
      echo "[SERVER] 未运行"
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;

  *)
    echo "用法: $0 {start|stop|restart|status} [port]"
    exit 2
    ;;
esac
