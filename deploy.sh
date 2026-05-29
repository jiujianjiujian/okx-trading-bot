#!/bin/bash
# VPS 一键部署 - SCP上传后自动执行
cd /root/okx-bot
sed -i 's|TELEGRAM_PROXY=.*|TELEGRAM_PROXY=|' .env
sed -i 's|PROXY_URL=.*|PROXY_URL=|' .env
./venv/bin/pip install -r requirements.txt -q 2>&1
./venv/bin/python -m compileall -q .
systemctl restart okx-bot
sleep 8
echo "=== 部署完成 ==="
systemctl --no-pager --full status okx-bot | tail -12
grep -E "清算|v5|全功能|启动完成|ERROR|NetworkError" /root/okx-bot/bot.log | tail -8
curl -s --max-time 5 http://127.0.0.1:8000/api/health
