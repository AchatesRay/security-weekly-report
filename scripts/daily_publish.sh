#!/usr/bin/env bash
# 日更发布脚本（2026-11-11 起：每周一 08:00 → 每天早上 08:00）
#
# 由 Hermes 调度器以「脚本模式（no_agent）」调用：
#     cd <生产目录> && exec bash scripts/daily_publish.sh
#
# 职责（单一入口，失败即停手，绝不半更新线上页面）：
#   1. 跑完整管道（venv/bin/python app.py --run），非 0 退出码 = 失败
#   2. 成功后原子替换稳定链接（桌面 / 移动）
#   3. 归档当日快照 cybersec_daily_<YYYYMMDD>.html（+ _mobile）
#   4. 清理超过保留期的存量（reports 数据/图片 + 归档页），不备份
#   5. 输出一行摘要给微信推送
#
# 保留期：脚本内 RETENTION_DAYS=14，与 config/settings.json 的
#         images.retention_days / report.rolling.window_days 保持一致。

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

PY="$PROJECT_ROOT/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

RETENTION_DAYS=14
STABLE_DIR="$(cd "$PROJECT_ROOT/.." && pwd)/zhuanjia"
ARCHIVE_DIR="$PROJECT_ROOT"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
EDITION="$(date '+%Y-%m-%d')"
SLUG="$(date '+%Y%m%d')"
LOG="/tmp/cybersec_daily_${SLUG}.log"
START=$SECONDS

# 原子发布：同目录临时文件 → chmod → mv（避免浏览器读到半截 HTML）
publish() {
    local src="$1" dst="$2"
    if [ ! -s "$src" ]; then
        echo "[WARN] 源文件缺失或为空，跳过：$src"
        return 1
    fi
    cp -f "$src" "$dst.tmp" && chmod 644 "$dst.tmp" && mv -f "$dst.tmp" "$dst"
}

echo "[1/4] 运行管道 ..."
"$PY" app.py --run >>"$LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "❌ 日更失败 rc=${RC}，线上报告未更新（日志 $LOG）"
    tail -n 20 "$LOG"
    exit "$RC"
fi

echo "[2/4] 发布稳定链接 ..."
mkdir -p "$STABLE_DIR"
publish "$PROJECT_ROOT/reports/Security_Reports.html" \
        "$STABLE_DIR/cybersec_report.html" \
    || { echo "❌ 桌面版发布失败，保留线上旧版"; exit 1; }
publish "$PROJECT_ROOT/reports/Security_Reports_mobile.html" \
        "$STABLE_DIR/cybersec_report_mobile.html" \
    || { echo "❌ 移动版发布失败，保留线上旧版"; exit 1; }

echo "[3/4] 归档当日快照 ..."
publish "$PROJECT_ROOT/reports/Security_Reports.html" \
        "$ARCHIVE_DIR/cybersec_daily_${SLUG}.html" \
    || { echo "❌ 桌面版归档失败"; exit 1; }
publish "$PROJECT_ROOT/reports/Security_Reports_mobile.html" \
        "$ARCHIVE_DIR/cybersec_daily_${SLUG}_mobile.html" \
    || { echo "❌ 移动版归档失败"; exit 1; }

echo "[4/4] 清理超过 ${RETENTION_DAYS} 天的存量 ..."
"$PY" app.py retention --days "$RETENTION_DAYS" >>"$LOG" 2>&1 \
    || echo "[WARN] 保留期清理失败，见 $LOG"
find "$ARCHIVE_DIR" -maxdepth 1 -type f \
    \( -name 'cybersec_daily_*.html' -o -name 'cybersec_weekly_*.html' \) \
    -mtime +"$RETENTION_DAYS" -delete 2>/dev/null

# 摘要：条目数取当日数据文件，取不到就留空
COUNT="$("$PY" - "$EDITION" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(f"reports/data_{sys.argv[1]}.json", encoding="utf-8") as f:
        print(len(json.load(f)))
except Exception:
    print("?")
PYEOF
)"

echo "✅ 日更完成 ${STAMP} 用时 $((SECONDS - START))s 窗口条目 ${COUNT} 条"
echo "   桌面 https://book.leich.top:8090/cybersec_report.html"
