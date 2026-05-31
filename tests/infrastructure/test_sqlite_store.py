"""SQLite 存储测试 — 内存数据库"""

import pytest
from src.core.models import TradeSignal, ScalpDecision, PnLRecord
from src.infrastructure.sqlite_store import SqliteStore


@pytest.fixture
def store():
    """使用 :memory: 避免影响真实数据库"""
    s = SqliteStore(":memory:")
    yield s
    s.close()


class TestSignalLogging:
    def test_log_signal(self, store):
        signal = TradeSignal(
            symbol="BTCUSDT",
            bybit_symbol="BTCUSDT",
            direction="long",
            price=87000.0,
            stop_loss=86500.0,
            take_profit=88000.0,
            strategy="TestStrategy",
            interval="5m",
            comment="测试",
            raw_data={"key": "value"},
        )
        sid = store.log_signal(signal)
        assert sid > 0

    def test_update_signal_status(self, store):
        signal = TradeSignal(
            symbol="ETHUSDT", bybit_symbol="ETHUSDT",
            direction="short", price=3000.0,
            stop_loss=3100.0, take_profit=2900.0,
            strategy="T", interval="1h", comment="",
            raw_data={},
        )
        sid = store.log_signal(signal)
        store.update_signal_status(sid, "sent")
        # 不应抛异常
        store.update_signal_status(sid, "closed_tp")


class TestDecisionLogging:
    def test_log_decision(self, store):
        dec = ScalpDecision(
            symbol="BTCUSDT", direction="long", action="LONG",
            entry=87000.0, stop_loss=86500.0, take_profit=88000.0,
            confidence=75, reason="看涨", scalping_strategy="momentum",
            sl_pct=0.3, tp_pct=0.5, risk_reward=1.67, net_risk_reward=2.1,
            position_size=100.0, risk_usdt=3.0, expected_profit_usdt=8.0,
            fee_cost=0.11,
        )
        did = store.log_decision(dec, 0)
        assert did > 0

    def test_get_recent_decisions(self, store):
        for i in range(5):
            dec = ScalpDecision(
                symbol=f"COIN{i}USDT", direction="long", action="LONG",
                entry=100.0, confidence=70 + i,
            )
            store.log_decision(dec, 0)
        results = store.get_recent_decisions(limit=3)
        assert len(results) == 3


class TestPnL:
    def test_log_pnl(self, store):
        rec = PnLRecord(
            symbol="BTCUSDT", direction="long",
            entry=87000.0, exit_price=87500.0,
            position_size=100.0, fee_paid=0.11,
            pnl_usdt=5.0, pnl_pct=0.5,
            closed_by="tp",
        )
        store.log_pnl(rec)
        pnl = store.get_today_pnl()
        assert pnl == pytest.approx(5.0)

    def test_no_trades_yet(self, store):
        stats = store.get_today_stats()
        assert stats["total_trades"] == 0
        assert stats["net_pnl_usdt"] == 0
