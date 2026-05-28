#!/bin/bash
# VPS 自动更新脚本 — 由 cron 每5分钟调用
REPO_DIR="/root/okx-bot"
BRANCH="master"
LOG_FILE="$REPO_DIR/auto_update.log"

cd "$REPO_DIR" || exit 1

git fetch origin "$BRANCH" 2>/dev/null
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse origin/"$BRANCH" 2>/dev/null)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 更新中..." >> "$LOG_FILE"
git reset --hard origin/"$BRANCH" 2>/dev/null
./venv/bin/pip install -r requirements.txt -q 2>> "$LOG_FILE"
rm -rf __pycache__
pkill -f "python main.py" 2>/dev/null
sleep 2
nohup ./venv/bin/python main.py > bot.log 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 部署完成" >> "$LOG_FILE"
