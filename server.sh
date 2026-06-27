#!/bin/bash
# 安全管理后台服务器管理脚本
# 用法: ./server.sh {start|stop|restart|status}

cd "$(dirname "$0")"
PID_FILE="/tmp/config_server.pid"
PORT=${2:-8081}
SERVER_SCRIPT="server/config_server.py"
LOG_FILE="/tmp/config_server_${PORT}.log"

# 检测端口是否被占用及占用进程 PID
port_pid() {
  ss -tlnp "sport = :${PORT}" 2>/dev/null | grep -oP 'pid=\K\d+' | head -1
}

case "${1:-status}" in
  start)
    existing=$(port_pid)
    if [ -n "$existing" ]; then
      echo "[SERVER] 已在运行 PID: $existing (端口 $PORT)"
      echo "$existing" > "$PID_FILE"
      exit 0
    fi
    # 清理过期 PID 文件
    rm -f "$PID_FILE"
    nohup python3 "$SERVER_SCRIPT" "$PORT" > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $! > "$PID_FILE"
    # 等待启动确认
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
      echo "[SERVER] 已启动 PID: $PID (端口 $PORT)"
    else
      echo "[SERVER] 启动失败，请查看日志: $LOG_FILE"
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;
  stop)
    existing=$(port_pid)
    if [ -n "$existing" ]; then
      kill "$existing" 2>/dev/null
      # 等待进程退出
      for i in 1 2 3; do
        sleep 1
        if [ -z "$(port_pid)" ]; then break; fi
      done
      echo "[SERVER] 已停止 PID: $existing"
    else
      echo "[SERVER] 未运行"
    fi
    rm -f "$PID_FILE"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start "$PORT"
    ;;
  status)
    existing=$(port_pid)
    if [ -n "$existing" ]; then
      echo "[SERVER] 运行中 PID: $existing"
      echo "$existing" > "$PID_FILE"
      curl -s --max-time 2 "http://localhost:$PORT/api/config/settings" | head -c 80
      echo ""
    else
      echo "[SERVER] 未运行"
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status} [port]"
    ;;
esac
