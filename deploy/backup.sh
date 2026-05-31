#!/bin/bash
# 数据库每日备份 — crontab: 0 3 * * * /root/okx-bot/deploy/backup.sh
BACKUP_DIR=/root/okx-bot/backups
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d)
cp /root/okx-bot/trades.db "$BACKUP_DIR/trades.db.$DATE"
# 保留最近30天
find "$BACKUP_DIR" -name "trades.db.*" -mtime +30 -delete
echo "$(date): 备份完成" >> "$BACKUP_DIR/backup.log"
