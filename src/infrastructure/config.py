"""全局配置 — 基于 .env 加载"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Bybit API ──────────────────────────────────────────
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")
BYBIT_DEMO = os.getenv("BYBIT_DEMO", "true").lower() == "true"
BYBIT_BASE_URL = "https://api.bybit.com"
BYBIT_TESTNET_URL = "https://api-testnet.bybit.com"

# ── 3Commas Signal Bot (TradingView Webhook 格式) ─────────
# 在 3Commas DCA Bot → Signals → 获取 Webhook URL 和 JWT Secret
THREECOMMAS_SECRET = os.getenv("THREECOMMAS_SECRET", "")            # JWT token
THREECOMMAS_BOT_UUID = os.getenv("THREECOMMAS_BOT_UUID", "")        # bot uuid
THREECOMMAS_WEBHOOK_URL = os.getenv("THREECOMMAS_WEBHOOK_URL", "")  # 3C 提供的 webhook 地址
THREECOMMAS_EXCHANGE = os.getenv("THREECOMMAS_EXCHANGE", "Bybit")   # 交易所名称

# ── TradingView Webhook ────────────────────────────────
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", WEBHOOK_SECRET)

# ── 剥头皮参数 ─────────────────────────────────────────
# 每日目标 USDT
DAILY_TARGET_USDT = float(os.getenv("DAILY_TARGET_USDT", "50.0"))
# 单笔最大亏损 USDT
MAX_LOSS_PER_TRADE_USDT = float(os.getenv("MAX_LOSS_PER_TRADE_USDT", "5.0"))
# 每日最大亏损 USDT（超过暂停交易）
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", "30.0"))
# 每日最大交易次数
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "20"))
# 最低盈亏比（扣费后）
MIN_NET_RR = float(os.getenv("MIN_NET_RR", "2.0"))

# 止损止盈（百分比，剥头皮用）
SCALP_SL_PCT_MIN = float(os.getenv("SCALP_SL_PCT_MIN", "0.15"))   # 最小止损 0.15%
SCALP_SL_PCT_MAX = float(os.getenv("SCALP_SL_PCT_MAX", "0.50"))   # 最大止损 0.50%
SCALP_TP_PCT_MIN = float(os.getenv("SCALP_TP_PCT_MIN", "0.30"))   # 最小止盈 0.30%
SCALP_TP_PCT_MAX = float(os.getenv("SCALP_TP_PCT_MAX", "1.50"))   # 最大止盈 1.50%

# 手续费（按 Bybit 合约 taker 0.055% / maker 0.02%）
FEE_TAKER_PCT = float(os.getenv("FEE_TAKER_PCT", "0.055"))
FEE_MAKER_PCT = float(os.getenv("FEE_MAKER_PCT", "0.020"))
ESTIMATED_FEE_PCT = float(os.getenv("ESTIMATED_FEE_PCT", "0.11"))  # 双向 taker 手续费

# AI 决策最低置信度（低于此值不交易）
MIN_AI_CONFIDENCE = int(os.getenv("MIN_AI_CONFIDENCE", "65"))

# 并发持仓上限
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))

# ── Telegram ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "")
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"

# ── DeepSeek AI ────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "2048"))

# ── 交易品种 (14币, 按流动性和波动性分层) ──
SCALP_UNIVERSE = [
    s.strip() for s in os.getenv(
        "SCALP_UNIVERSE",
        "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT,LINKUSDT,ADAUSDT,"
        "AVAXUSDT,DOGEUSDT,SUIUSDT,APTUSDT,INJUSDT,WIFUSDT,PEPEUSDT",
    ).split(",") if s.strip()
]

# 杠杆 — 逐仓 10x
SCALP_LEVERAGE = int(os.getenv("SCALP_LEVERAGE", "10"))

# ── 代理 ───────────────────────────────────────────────
PROXY_URL = os.getenv("PROXY_URL", "")

# ── 数据库 ─────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "trades.db")

# ── AI 自主交易 ────────────────────────────────────────
AI_AUTO_START = os.getenv("AI_AUTO_START", "false").lower() == "true"
