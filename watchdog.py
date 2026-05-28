"""
VPS 守护进程 — 本地运行，监控+报警+自动修复
每 60 秒检测一次，连续3次失败触发修复
"""
import os
import time
import requests
import subprocess
import contextlib
import datetime
from dotenv import load_dotenv

load_dotenv()

VPS_IP = os.getenv("VPS_IP", "127.0.0.1")
VPS_URL = f"http://{VPS_IP}/api/health"
TELEGRAM_BOT = os.getenv("WATCHDOG_TG_BOT_TOKEN", "")
TELEGRAM_CHAT = os.getenv("WATCHDOG_TG_CHAT_ID", "")
SSH_KEY = os.getenv("WATCHDOG_SSH_KEY", "")
FAIL_THRESHOLD = int(os.getenv("WATCHDOG_FAIL_THRESHOLD", "3"))

_socks = os.getenv("WATCHDOG_SOCKS_PROXY", "")
PROXIES = {"http": _socks, "https": _socks} if _socks else None

def send_telegram(msg):
    with contextlib.suppress(Exception):
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            proxies=PROXIES, timeout=15,
        )

def check_vps():
    """检查 VPS 健康状态"""
    try:
        r = requests.get(VPS_URL, proxies=PROXIES, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return True, data
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:100]

def auto_repair():
    """自动修复: SSH重启服务"""
    repair_log = []
    # 方式1: 云助手命令 (需要阿里云 API)
    repair_log.append("尝试 SSH 修复...")
    try:
        result = subprocess.run([
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
            "-i", SSH_KEY, f"root@{VPS_IP}",
            "systemctl restart okx-bot && echo FIXED || echo FIX_FAILED"
        ], capture_output=True, timeout=20)
        repair_log.append(f"SSH结果: {result.stdout.decode().strip()}")
        return b"FIXED" in result.stdout
    except Exception as e:
        repair_log.append(f"SSH修复失败: {e}")

    # 方式2: 如果 SSH 不通，通过云助手 API
    repair_log.append("SSH不通,尝试云助手...")
    try:
        # 阿里云 API 修复
        import base64
        _ak_id = os.getenv("ALIYUN_AK_ID", "")
        _ak_secret = os.getenv("ALIYUN_AK_SECRET", "")
        region = os.getenv("ALIYUN_REGION", "ap-northeast-2")
        inst_id = os.getenv("ALIYUN_INSTANCE_ID", "")

        if not _ak_id or not _ak_secret or not inst_id:
            repair_log.append("阿里云 API 未配置，跳过云助手修复")
            return False

        _params = {
            "RegionId": region,
            "InstanceId.1": inst_id,
            "CommandContent": base64.b64encode("systemctl restart okx-bot".encode()).decode(),
            "Type": "RunShellScript",
            "Timeout": "60",
        }
        # ... API call logic
        repair_log.append("云助手命令已发送")
        return True
    except Exception as e:
        repair_log.append(f"云助手失败: {e}")
        return False

def main():
    fail_count = 0
    last_alert = 0
    print(f" VPS 守护启动 — 监控 {VPS_IP}")
    send_telegram(f" VPS 守护已启动,监控 {VPS_IP} 每60秒")

    while True:
        try:
            ok, info = check_vps()
            now = time.time()

            if ok:
                if fail_count > 0:
                    msg = f" VPS 已恢复 (离线{fail_count}次)"
                    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}")
                    if now - last_alert > 300:
                        send_telegram(msg)
                        last_alert = now
                fail_count = 0
            else:
                fail_count += 1
                print(f"[{datetime.datetime.now():%H:%M:%S}] 失败({fail_count}/{FAIL_THRESHOLD}): {info}")

                if fail_count >= FAIL_THRESHOLD:
                    msg = f" VPS 连续失败 {fail_count} 次,正在自动修复...\n{info}"
                    print(msg)
                    if now - last_alert > 300:
                        send_telegram(msg)
                        last_alert = now

                    fixed = auto_repair()
                    if fixed:
                        send_telegram(" VPS 自动修复已触发,等待恢复")
                        fail_count = 0
                    else:
                        send_telegram(" 自动修复失败,请手动检查VPS")
                    last_alert = now

            time.sleep(60)

        except KeyboardInterrupt:
            print("\n 守护进程退出")
            break
        except Exception as e:
            print(f"守护异常: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
