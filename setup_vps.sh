#!/bin/bash
# OKX 交易机器人 - 一键安装脚本 (VPS 端)
set -e

echo "=== 1. 安装依赖 ==="
apt update -qq
apt install -y -qq python3.12-venv python3-pip

echo "=== 2. 创建虚拟环境 ==="
cd /root/okx-bot
python3 -m venv venv

echo "=== 3. 安装 Python 包 ==="
venv/bin/pip install -r requirements.txt -q

echo "=== 4. 启动服务 ==="
nohup venv/bin/python main.py > /root/bot.log 2>&1 &
echo "PID: $!"

sleep 3
echo "=== 5. 检查状态 ==="
cat /root/bot.log

echo ""
echo "=== 安装完成 ==="
echo "Webhook: http://43.108.48.96:8000/webhook"
echo "健康检查: http://43.108.48.96:8000/api/health"
