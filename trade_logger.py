"""
交易日志 - SQLite 数据库存储所有交易记录
"""

import sqlite3
import json
import threading
from datetime import datetime, date, timedelta
from config import DB_PATH


class TradeLogger:
    """交易记录持久化存储"""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
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
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                time        TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                mode        TEXT NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                confidence  INTEGER,
                risk_reward REAL,
                entry       REAL,
                stop_loss   REAL,
                take_profit REAL,
                raw_data    TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_orders (
                order_id    TEXT PRIMARY KEY,
                time        TEXT NOT NULL,
                update_time TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                mode        TEXT,
                direction   TEXT,
                entry       REAL,
                quantity    REAL,
                leverage    INTEGER,
                stop_loss   REAL,
                take_profit REAL,
                status      TEXT NOT NULL,
                trade_id    INTEGER,
                raw_data    TEXT
            )
        """)
        self.conn.commit()

    # ----------------------------------------------------------------
    # 信号记录
    # ----------------------------------------------------------------

    def log_signal(self, signal) -> int:
        """记录收到的信号，返回信号ID"""
        now = datetime.now().isoformat()
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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

    def log_auto_trade(self, decision: dict, quantity: float, order_id: str) -> int:
        """记录 AI 自主交易开仓。"""
        now = datetime.now().isoformat()
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO trades (signal_id, time, symbol, direction, entry_price,
                   quantity, leverage, stop_loss, take_profit, order_id, status)
                   VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
                (
                    now,
                    decision.get("symbol"),
                    decision.get("direction"),
                    decision.get("entry"),
                    quantity,
                    decision.get("leverage"),
                    decision.get("stop_loss"),
                    decision.get("take_profit"),
                    order_id,
                ),
            )
            self.conn.commit()
        return cursor.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, pnl: float):
        """标记交易已平仓"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute(
                "UPDATE trades SET status='closed', exit_price=?, pnl=?, close_time=? WHERE id=?",
                (exit_price, pnl, now, trade_id),
            )
            self.conn.commit()

    # ----------------------------------------------------------------
    # AI 订单状态
    # ----------------------------------------------------------------

    def log_ai_order(self, decision: dict, quantity: float, order_id: str, status: str = "submitted"):
        """记录 AI 限价单生命周期。"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO ai_orders (order_id, time, update_time, symbol,
                   mode, direction, entry, quantity, leverage, stop_loss, take_profit,
                   status, trade_id, raw_data)
                   VALUES (?, COALESCE((SELECT time FROM ai_orders WHERE order_id=?), ?),
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    order_id,
                    now,
                    now,
                    decision.get("symbol"),
                    decision.get("mode"),
                    decision.get("direction"),
                    decision.get("entry"),
                    quantity,
                    decision.get("leverage"),
                    decision.get("stop_loss"),
                    decision.get("take_profit"),
                    status,
                    decision.get("trade_id"),
                    json.dumps(decision, ensure_ascii=False, default=str),
                ),
            )
            self.conn.commit()

    def update_ai_order(self, order_id: str, status: str, trade_id: int | None = None):
        """更新 AI 限价单状态。"""
        now = datetime.now().isoformat()
        with self._lock:
            self.conn.execute(
                """UPDATE ai_orders
                   SET status=?, trade_id=COALESCE(?, trade_id), update_time=?
                   WHERE order_id=?""",
                (status, trade_id, now, order_id),
            )
            self.conn.commit()

    def get_active_ai_orders(self, max_age_hours: int = 24) -> list:
        """恢复仍可能活跃的 AI 限价单。"""
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        cursor = self.conn.execute(
            """SELECT order_id, time, symbol, mode, direction, entry, quantity,
                      leverage, stop_loss, take_profit, status, trade_id, raw_data
               FROM ai_orders
               WHERE status IN ('submitted', 'cancel_failed')
                 AND update_time>=?
               ORDER BY time DESC""",
            (cutoff,),
        )
        return cursor.fetchall()

    def get_symbol_loss_stats(self, symbol: str, hours: int = 24) -> dict:
        """统计单币近期亏损，用于冷却保护。"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = self.conn.execute(
            """SELECT close_time, pnl FROM trades
               WHERE symbol=? AND status='closed' AND close_time>=?
               ORDER BY close_time DESC""",
            (symbol, cutoff),
        )
        rows = cursor.fetchall()
        losses = [(t, p) for t, p in rows if (p or 0) < 0]
        minutes_since_loss = None
        if losses:
            try:
                last_loss = datetime.fromisoformat(losses[0][0])
                minutes_since_loss = (datetime.now() - last_loss).total_seconds() / 60
            except (TypeError, ValueError):
                minutes_since_loss = None
        return {
            "losses": len(losses),
            "minutes_since_loss": minutes_since_loss,
            "last_loss_pnl": losses[0][1] if losses else 0,
        }

    # ----------------------------------------------------------------
    # AI 决策审计
    # ----------------------------------------------------------------

    def log_ai_decision(self, symbol: str, mode: str, decision: dict | None):
        """持久化每次 AI 扫描结果，包含 WAIT 原因。"""
        now = datetime.now().isoformat()
        data = decision or {}
        action = data.get("action", "NONE")
        with self._lock:
            self.conn.execute(
                """INSERT INTO ai_decisions (time, symbol, mode, action, reason,
                   confidence, risk_reward, entry, stop_loss, take_profit, raw_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    symbol,
                    mode,
                    action,
                    data.get("reason", ""),
                    data.get("confidence"),
                    data.get("risk_reward"),
                    data.get("entry"),
                    data.get("stop_loss"),
                    data.get("take_profit"),
                    json.dumps(data, ensure_ascii=False, default=str),
                ),
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

    def get_closed_trade_stats(self, symbol: str | None = None, days: int = 60) -> dict:
        """获取近期已平仓交易统计，用于仓位胜率校准。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        params: list = [cutoff]
        where = "status='closed' AND close_time>=?"
        if symbol:
            where += " AND symbol=?"
            params.append(symbol)
        cursor = self.conn.execute(
            f"""SELECT pnl FROM trades
                WHERE {where}
                ORDER BY close_time DESC""",
            params,
        )
        pnls = [float(row[0] or 0) for row in cursor.fetchall()]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        total = len(pnls)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        return {
            "total": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / total if total else 0.0,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff_ratio,
        }

    def get_recent_ai_decisions(self, limit: int = 100) -> list:
        """获取最近 AI 决策审计记录。"""
        limit = max(1, min(int(limit), 500))
        cursor = self.conn.execute(
            """SELECT time, symbol, mode, action, reason, confidence, risk_reward
               FROM ai_decisions ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return cursor.fetchall()

    def get_ai_decision_stats(self, hours: int = 24) -> dict:
        """统计最近一段时间 AI 扫描通过率和主要观望原因。"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = self.conn.execute(
            """SELECT mode, action, reason FROM ai_decisions
               WHERE time>=? ORDER BY id DESC""",
            (cutoff,),
        )
        rows = cursor.fetchall()
        by_mode = {}
        reasons = {}
        for mode, action, reason in rows:
            stats = by_mode.setdefault(mode, {"total": 0, "wait": 0, "trade": 0, "none": 0})
            stats["total"] += 1
            if action in ("LONG", "SHORT"):
                stats["trade"] += 1
            elif action == "WAIT":
                stats["wait"] += 1
            else:
                stats["none"] += 1
            if action == "WAIT" and reason:
                key = str(reason)[:80]
                reasons[key] = reasons.get(key, 0) + 1
        top_reasons = sorted(reasons.items(), key=lambda x: -x[1])[:10]
        return {"hours": hours, "total": len(rows), "by_mode": by_mode, "top_wait_reasons": top_reasons}

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
