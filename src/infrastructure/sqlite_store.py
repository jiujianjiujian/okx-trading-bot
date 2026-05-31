"""SQLite 存储实现 — 实现 SignalStore 接口

表结构:
- signals: TradingView 信号记录
- decisions: AI 决策记录
- pnl_records: 盈亏记录
- daily_stats: 每日统计缓存

与旧版 trades.db 兼容: signals 表列名相同, trades → pnl_records 扩展
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from ..core.models import TradeSignal, ScalpDecision, PnLRecord
from ..core.interfaces import SignalStore
from .config import DB_PATH
from .logging_ import get_logger

logger = get_logger(__name__)


class SqliteStore(SignalStore):
    """SQLite 数据库, 线程安全"""

    def __init__(self, db_path: str | None = None):
        self._path = db_path or DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_tables()
        return self._conn

    def _init_tables(self):
        c = self._conn.cursor()

        # 信号表 (兼容旧版)
        c.execute("""CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            okx_symbol TEXT,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            strategy TEXT,
            interval TEXT,
            comment TEXT,
            raw_data TEXT,
            status TEXT DEFAULT 'pending'
        )""")

        # AI 决策表
        c.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            signal_id INTEGER DEFAULT 0,
            symbol TEXT NOT NULL,
            mode TEXT DEFAULT 'scalp',
            action TEXT NOT NULL,
            direction TEXT,
            confidence INTEGER DEFAULT 0,
            reason TEXT,
            scalping_strategy TEXT,
            entry REAL DEFAULT 0,
            stop_loss REAL DEFAULT 0,
            take_profit REAL DEFAULT 0,
            sl_pct REAL DEFAULT 0,
            tp_pct REAL DEFAULT 0,
            risk_reward REAL DEFAULT 0,
            net_risk_reward REAL DEFAULT 0,
            position_size REAL DEFAULT 0,
            risk_usdt REAL DEFAULT 0,
            expected_profit_usdt REAL DEFAULT 0,
            fee_cost REAL DEFAULT 0,
            raw_json TEXT
        )""")

        # 盈亏记录表
        c.execute("""CREATE TABLE IF NOT EXISTS pnl_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT DEFAULT '',
            entry REAL DEFAULT 0,
            exit_price REAL DEFAULT 0,
            position_size REAL DEFAULT 0,
            fee_paid REAL DEFAULT 0,
            pnl_usdt REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            closed_by TEXT DEFAULT '',
            signal_id INTEGER DEFAULT 0,
            order_id TEXT DEFAULT ''
        )""")
        # 唯一索引 — 防 PnL 同步重复
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_order_id ON pnl_records(order_id) WHERE order_id != ''")

        c.connection.commit()

    # ── 写入 ──────────────────────────────────────────

    def log_signal(self, signal: TradeSignal) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                """INSERT INTO signals (time, symbol, okx_symbol, direction, price,
                   stop_loss, take_profit, strategy, interval, comment, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (now, signal.symbol, signal.bybit_symbol, signal.direction,
                 signal.price, signal.stop_loss, signal.take_profit,
                 signal.strategy, signal.interval, signal.comment,
                 json.dumps(signal.raw_data)),
            )
            c.connection.commit()
            return c.lastrowid or 0

    def log_decision(self, decision: ScalpDecision, signal_id: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                """INSERT INTO decisions (
                    time, signal_id, symbol, mode, action, direction, confidence,
                    reason, scalping_strategy, entry, stop_loss, take_profit,
                    sl_pct, tp_pct, risk_reward, net_risk_reward,
                    position_size, risk_usdt, expected_profit_usdt, fee_cost, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, signal_id, decision.symbol, decision.mode, decision.action,
                 decision.direction, decision.confidence, decision.reason,
                 decision.scalping_strategy, decision.entry, decision.stop_loss,
                 decision.take_profit, decision.sl_pct, decision.tp_pct,
                 decision.risk_reward, decision.net_risk_reward,
                 decision.position_size, decision.risk_usdt,
                 decision.expected_profit_usdt, decision.fee_cost,
                 json.dumps(decision.threecommas_signal or {})),
            )
            c.connection.commit()
            return c.lastrowid or 0

    def update_signal_status(self, signal_id: int, status: str) -> None:
        with self._lock:
            c = self.conn.cursor()
            c.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))
            c.connection.commit()

    def log_pnl(self, record: PnLRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        order_id = getattr(record, 'order_id', '')
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                """INSERT OR IGNORE INTO pnl_records (
                    time, symbol, direction, entry, exit_price, position_size,
                    fee_paid, pnl_usdt, pnl_pct, closed_by, signal_id, order_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, record.symbol, record.direction, record.entry,
                 record.exit_price, record.position_size, record.fee_paid,
                 record.pnl_usdt, record.pnl_pct, record.closed_by,
                 record.signal_id, order_id),
            )
            c.connection.commit()

    # ── 查询 ──────────────────────────────────────────

    def get_today_pnl(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT SUM(pnl_usdt) FROM pnl_records WHERE time LIKE ?",
                (f"{today}%",),
            )
            row = c.fetchone()
            return float(row[0]) if row and row[0] else 0.0

    def get_today_trades(self) -> list[dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT * FROM pnl_records WHERE time LIKE ? ORDER BY id DESC",
                (f"{today}%",),
            )
            return [dict(r) for r in c.fetchall()]

    def get_today_stats(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT pnl_usdt, fee_paid FROM pnl_records WHERE time LIKE ?",
                (f"{today}%",),
            )
            rows = [dict(r) for r in c.fetchall()]

        total_trades = len(rows)
        wins = sum(1 for r in rows if r["pnl_usdt"] > 0)
        losses = sum(1 for r in rows if r["pnl_usdt"] <= 0)
        total_pnl = sum(r["pnl_usdt"] for r in rows)
        total_fees = sum(r["fee_paid"] for r in rows)
        net_pnl = total_pnl - total_fees
        best = max(r["pnl_usdt"] for r in rows) if rows else 0.0
        worst = min(r["pnl_usdt"] for r in rows) if rows else 0.0
        avg_rr = (total_pnl / total_trades) if total_trades > 0 else 0.0

        return {
            "date": today,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl_usdt": round(total_pnl, 2),
            "total_fees_usdt": round(total_fees, 2),
            "net_pnl_usdt": round(net_pnl, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "avg_rr": round(avg_rr, 2),
            "target_50_usdt": net_pnl >= 50,
        }

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM pnl_records ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in c.fetchall()]

    def get_recent_decisions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            c = self.conn.cursor()
            c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in c.fetchall()]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
