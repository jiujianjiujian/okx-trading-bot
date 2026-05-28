"""
交易日志 - SQLite 数据库存储所有交易记录
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from config import DB_PATH


class TradeLogger:
    """交易记录持久化存储"""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        """建表"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                time        TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                price       REAL NOT NULL,
                stop_loss   REAL,
                take_profit REAL,
                strategy    TEXT,
                interval    TEXT,
                comment     TEXT,
                raw_data    TEXT,
                status      TEXT DEFAULT 'received'
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER,
                time        TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                direction   TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity    INTEGER NOT NULL,
                leverage    INTEGER NOT NULL,
                stop_loss   REAL,
                take_profit REAL,
                order_id    TEXT,
                status      TEXT DEFAULT 'open',
                exit_price  REAL,
                pnl         REAL,
                close_time  TEXT,
                FOREIGN KEY (signal_id) REFERENCES signals(id)
            )
        """)
        self.conn.commit()

    # ----------------------------------------------------------------
    # 信号记录
    # ----------------------------------------------------------------

    def log_signal(self, signal) -> int:
        """记录收到的信号，返回信号ID"""
        now = datetime.now().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO signals (time, symbol, direction, price, stop_loss,
               take_profit, strategy, interval, comment, raw_data, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received')""",
            (
                now,
                signal.okx_symbol,
                signal.direction,
                signal.price,
                signal.stop_loss,
                signal.take_profit,
                signal.strategy,
                signal.interval,
                signal.comment,
                json.dumps(signal.raw_data, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_signal_status(self, signal_id: int, status: str):
        """更新信号处理状态"""
        self.conn.execute(
            "UPDATE signals SET status=? WHERE id=?", (status, signal_id)
        )
        self.conn.commit()

    # ----------------------------------------------------------------
    # 交易记录
    # ----------------------------------------------------------------

    def log_trade(self, signal_id: int, signal, quantity: int, leverage: int, order_id: str) -> int:
        """记录新开仓"""
        now = datetime.now().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO trades (signal_id, time, symbol, direction, entry_price,
               quantity, leverage, stop_loss, take_profit, order_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (
                signal_id,
                now,
                signal.okx_symbol,
                signal.direction,
                signal.price,
                quantity,
                leverage,
                signal.stop_loss,
                signal.take_profit,
                order_id,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, pnl: float):
        """标记交易已平仓"""
        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE trades SET status='closed', exit_price=?, pnl=?, close_time=? WHERE id=?",
            (exit_price, pnl, now, trade_id),
        )
        self.conn.commit()

    # ----------------------------------------------------------------
    # 查询
    # ----------------------------------------------------------------

    def get_open_trades(self) -> list:
        """获取所有未平仓交易"""
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY time DESC"
        )
        return cursor.fetchall()

    def get_today_pnl(self) -> float:
        """获取今日总盈亏"""
        today = date.today().isoformat()
        cursor = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' AND date(close_time)=?",
            (today,),
        )
        return cursor.fetchone()[0]

    def get_today_trades(self) -> list:
        """获取今日所有交易"""
        today = date.today().isoformat()
        cursor = self.conn.execute(
            "SELECT * FROM trades WHERE date(time)=? ORDER BY time DESC",
            (today,),
        )
        return cursor.fetchall()

    def get_session_stats(self, days: int = 30) -> dict:
        """统计各时段胜率 (北京时间)"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self.conn.execute(
            "SELECT time, pnl FROM trades WHERE status='closed' AND close_time>=?",
            (cutoff,),
        )
        rows = cursor.fetchall()
        sessions = {}
        for time_str, pnl in rows:
            try:
                dt = datetime.fromisoformat(time_str)
                hour = (dt.hour + 8) % 24
                if dt.weekday() >= 5:
                    sess = "weekend"
                elif 8 <= hour < 15:
                    sess = "asia"
                elif 15 <= hour < 23:
                    sess = "overlap"
                else:
                    sess = "us"
            except (ValueError, TypeError):
                continue
            if sess not in sessions:
                sessions[sess] = {"wins": 0, "total": 0, "pnl": 0.0}
            sessions[sess]["total"] += 1
            sessions[sess]["pnl"] += (pnl or 0)
            if (pnl or 0) > 0:
                sessions[sess]["wins"] += 1
        result = {}
        for sess, stats in sessions.items():
            result[sess] = {
                "total": stats["total"],
                "wins": stats["wins"],
                "pnl": round(stats["pnl"], 2),
                "win_rate": stats["wins"] / stats["total"] if stats["total"] > 0 else 0.5,
            }
        return result

    def get_trade_stats(self) -> dict:
        """获取交易统计"""
        cursor = self.conn.execute(
            "SELECT COUNT(*), SUM(pnl), "
            "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pnl<0 THEN 1 ELSE 0 END) "
            "FROM trades WHERE status='closed'"
        )
        total, total_pnl, wins, losses = cursor.fetchone()
        total = total or 0
        wins = wins or 0
        losses = losses or 0
        return {
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "win_rate": f"{wins / total * 100:.1f}%" if total > 0 else "N/A",
            "total_pnl": f"{total_pnl or 0:.2f} USDT",
        }

    def close(self):
        self.conn.close()
