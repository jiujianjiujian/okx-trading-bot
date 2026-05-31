"""统一日志配置 — 文件轮转 + 控制台输出"""

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    """获取预配置的 logger，每个模块一个"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 控制台
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(_LOG_FORMAT)
    logger.addHandler(sh)

    # 文件轮转 (5MB × 5)
    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_LOG_FORMAT)
    logger.addHandler(fh)

    return logger


# 禁止第三方库的 DEBUG 日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
