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
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "")  # 通用代理，OKX 等使用

# ============================================
# 交易风控参数
# ============================================
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "2.0"))      # 单笔风险 %
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))      # 默认杠杆
MIN_LEVERAGE = int(os.getenv("MIN_LEVERAGE", "10"))              # 允许的最低杠杆
MAX_LEVERAGE = int(os.getenv("MAX_LEVERAGE", "25"))              # 允许的最高杠杆
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))             # 最大持仓数
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "5.0"))       # 日内最大亏损 %
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"  # 是否自动下单
AI_AUTO_START = os.getenv("AI_AUTO_START", "false").lower() == "true"  # 启动时自动开启AI自主交易
TRADING_UNIVERSE = os.getenv("TRADING_UNIVERSE", "core").lower()  # core=主流币, dynamic=全市场筛选
ESTIMATED_FEE_RATE = float(os.getenv("ESTIMATED_FEE_RATE", "0.0005"))  # 单边费率估计
ESTIMATED_SLIPPAGE_RATE = float(os.getenv("ESTIMATED_SLIPPAGE_RATE", "0.0002"))  # 单边滑点估计
MIN_NET_RR = float(os.getenv("MIN_NET_RR", "3.0"))                # 扣成本后最低盈亏比
SYMBOL_COOLDOWN_MINUTES = int(os.getenv("SYMBOL_COOLDOWN_MINUTES", "120"))  # 单币亏损冷却
SYMBOL_MAX_DAILY_LOSSES = int(os.getenv("SYMBOL_MAX_DAILY_LOSSES", "2"))    # 单币日内最大亏损次数
MARKET_GRAPH_ENABLED = os.getenv("MARKET_GRAPH_ENABLED", "true").lower() == "true"  # 图谱共振闸门
MARKET_GRAPH_MIN_EDGE = float(os.getenv("MARKET_GRAPH_MIN_EDGE", "60"))             # 最低方向优势
MARKET_GRAPH_MAX_CONFLICT = float(os.getenv("MARKET_GRAPH_MAX_CONFLICT", "35"))     # 最大信号冲突
MARKET_GRAPH_MIN_LIQUIDITY = float(os.getenv("MARKET_GRAPH_MIN_LIQUIDITY", "55"))   # 最低流动性评分
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))                         # 分数凯利比例
KELLY_PRIOR_WIN_RATE = float(os.getenv("KELLY_PRIOR_WIN_RATE", "0.45"))             # 样本不足时先验胜率
KELLY_PRIOR_TRADES = int(os.getenv("KELLY_PRIOR_TRADES", "20"))                     # 先验样本权重
KELLY_NO_HISTORY_CONF_CAP = float(os.getenv("KELLY_NO_HISTORY_CONF_CAP", "0.52"))   # 无历史时AI胜率上限
KELLY_MIN_EDGE_PCT = float(os.getenv("KELLY_MIN_EDGE_PCT", "2.0"))                  # 高于盈亏平衡的最小优势
KELLY_MAX_RISK_PCT = float(os.getenv("KELLY_MAX_RISK_PCT", "0.75"))                 # 凯利单笔风险上限%
MIN_EFFECTIVE_NOTIONAL = float(os.getenv("MIN_EFFECTIVE_NOTIONAL", "20"))           # 最小有效名义价值USDT
MIN_AVAILABLE_BALANCE = float(os.getenv("MIN_AVAILABLE_BALANCE", "10"))             # 最低可用余额USDT
MAX_TRADE_MARGIN_USAGE_PCT = float(os.getenv("MAX_TRADE_MARGIN_USAGE_PCT", "30"))   # 单笔最多占用可用保证金%

# ============================================
# Webhook 配置
# ============================================
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", WEBHOOK_SECRET)

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
