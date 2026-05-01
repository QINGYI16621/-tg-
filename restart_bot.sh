#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/www/wwwroot/TelegramPrivateVault}"
LOG_FILE="${LOG_FILE:-bot.log}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

echo "==> stopping old bot processes..."
pkill -9 -f "$APP_DIR/bot.py" 2>/dev/null || true
pkill -9 -f "python3 bot.py" 2>/dev/null || true
sleep 2

echo "==> cleaning stale sqlite/session files..."
rm -f bot.lock *.session-journal *.session-wal *.session-shm

echo "==> starting bot..."
nohup "$PYTHON_BIN" bot.py >> "$LOG_FILE" 2>&1 &
sleep 5

echo "==> current process:"
ps aux | grep '[b]ot.py' || true

echo "==> recent log:"
tail -n 80 "$LOG_FILE"
