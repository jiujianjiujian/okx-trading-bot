#!/bin/bash
# VPS 一键部署 - SCP上传后自动执行
cd /root/okx-bot
sed -i 's|TELEGRAM_PROXY=.*|TELEGRAM_PROXY=|' .env
sed -i 's|PROXY_URL=.*|PROXY_URL=|' .env
./venv/bin/pip install websocket-client -q 2>&1
pkill -f "python main.py" 2>/dev/null
sleep 2
nohup ./venv/bin/python main.py > /root/okx-bot/bot.log 2>&1 &
sleep 8
echo "=== 部署完成 ==="
grep -E "清算|v5|全功能|启动完成|ERROR|NetworkError" /root/okx-bot/bot.log | tail -8
curl -s --max-time 5 http://127.0.0.1:8000/api/health
