#!/bin/bash
# OKX 交易机器人 - VPS 部署脚本
# 在本地运行此脚本，将代码推送到 VPS 并重启服务
set -e

VPS_HOST="43.108.48.96"
VPS_USER="root"
VPS_PATH="/root/okx-bot"

echo "=== 1. 推送更新文件 ==="
scp auto_trader.py ${VPS_USER}@${VPS_HOST}:${VPS_PATH}/
scp telegram_bot.py ${VPS_USER}@${VPS_HOST}:${VPS_PATH}/
scp .env ${VPS_USER}@${VPS_HOST}:${VPS_PATH}/

echo "=== 2. 重启机器人 ==="
ssh ${VPS_USER}@${VPS_HOST} << 'REMOTE'
cd /root/okx-bot
pkill -f "python main.py" 2>/dev/null || true
sleep 2
nohup ./venv/bin/python main.py > /root/okx-bot/bot.log 2>&1 &
sleep 5
echo "=== 启动状态 ==="
grep -E "\[Telegram\]|\[启动\]|ERROR|启动完成|API" /root/okx-bot/bot.log | tail -10
echo "=== 健康检查 ==="
curl -s http://127.0.0.1:8000/api/health || echo "API 未响应"
REMOTE

echo ""
echo "=== 部署完成 ==="
echo "健康检查: http://${VPS_HOST}:8000/api/health"
echo "仪表盘:   http://${VPS_HOST}:8000/dashboard"
echo "查看日志: ssh ${VPS_USER}@${VPS_HOST} 'tail -f /root/okx-bot/bot.log'"
