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

echo "=== 4. 安装 systemd 服务 ==="
cat > /etc/systemd/system/okx-bot.service <<'SERVICE'
[Unit]
Description=OKX Trading Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/okx-bot
ExecStart=/root/okx-bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable okx-bot
systemctl restart okx-bot

sleep 3
echo "=== 5. 检查状态 ==="
systemctl --no-pager --full status okx-bot | tail -12
cat /root/okx-bot/bot.log

echo ""
echo "=== 安装完成 ==="
echo "Webhook: http://43.108.48.96:8000/webhook"
echo "健康检查: http://43.108.48.96:8000/api/health"
