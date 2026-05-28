"""
配置管理 - 从 .env 文件加载所有配置项
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ============================================
# OKX API 配置
# ============================================
OKX_API_KEY = os.getenv("OKX_API_KEY", "")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
OKX_DEMO = os.getenv("OKX_DEMO", "true").lower() == "true"
OKX_BASE_URL = "https://www.okx.com"

# ============================================
# Telegram 配置
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "")
PROXY_URL = os.getenv("PROXY_URL", "")  # 通用代理，OKX 等使用

# ============================================
# 交易风控参数
# ============================================
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "2.0"))      # 单笔风险 %
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "3"))       # 默认杠杆
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))             # 最大持仓数
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "5.0"))       # 日内最大亏损 %
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"  # 是否自动下单
AI_AUTO_START = os.getenv("AI_AUTO_START", "false").lower() == "true"  # 启动时自动开启AI自主交易

# ============================================
# Webhook 配置
# ============================================
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ============================================
# ============================================
# DeepSeek API
# ============================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# ============================================
# 数据库
# ============================================
DB_PATH = "trades.db"

# ============================================
# QQ Bot (NapCatQQ OneBot v11) 配置
# ============================================
QQ_BOT_ENABLED = os.getenv("QQ_BOT_ENABLED", "false").lower() == "true"
QQ_WS_HOST = os.getenv("QQ_WS_HOST", "0.0.0.0")
QQ_WS_PORT = int(os.getenv("QQ_WS_PORT", "8080"))
QQ_WS_TOKEN = os.getenv("QQ_WS_TOKEN", "")
QQ_ADMIN_ID = os.getenv("QQ_ADMIN_ID", "")
QQ_ADMIN_IDS = [u.strip() for u in os.getenv("QQ_ADMIN_IDS", QQ_ADMIN_ID).split(",") if u.strip()]
QQ_GROUP_ID = os.getenv("QQ_GROUP_ID", "")
QQ_BOT_QQ = os.getenv("QQ_BOT_QQ", "")

# ============================================
# Anthropic Claude API 配置
# ============================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024"))

# ============================================
# 会话管理配置
# ============================================
CONVERSATION_DB_PATH = os.getenv("CONVERSATION_DB_PATH", "conversations.json")
MAX_CONVERSATION_HISTORY = int(os.getenv("MAX_CONVERSATION_HISTORY", "20"))
