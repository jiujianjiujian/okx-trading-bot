#!/bin/bash
# 一键 Git 部署体系搭建 — 在本地运行，只需一次 SSH

VPS="root@43.108.48.96"
KEY="$HOME/.ssh/id_rsa"

echo "=== 1. VPS 上创建 bare 仓库 ==="
ssh -i "$KEY" "$VPS" "git init --bare /root/okx-bot.git"

echo "=== 2. 配置 post-receive 自动部署钩子 ==="
ssh -i "$KEY" "$VPS" "cat > /root/okx-bot.git/hooks/post-receive << 'HOOK'
#!/bin/bash
DEPLOY_DIR=/root/okx-bot
LOG=/root/okx-bot/auto_update.log
echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] 收到推送\" >> \$LOG
git --work-tree=\$DEPLOY_DIR --git-dir=/root/okx-bot.git checkout -f master
rm -rf \$DEPLOY_DIR/__pycache__
pkill -f 'python main.py' 2>/dev/null
sleep 2
cd \$DEPLOY_DIR && nohup ./venv/bin/python main.py > bot.log 2>&1 &
echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] 部署完成\" >> \$LOG
HOOK
chmod +x /root/okx-bot.git/hooks/post-receive"

echo "=== 3. 添加 VPS 远程仓库 ==="
git remote add vps "ssh://$VPS/root/okx-bot.git" 2>/dev/null || echo "远程 vps 已存在"

echo "=== 完成! 以后部署: git push vps master ==="
