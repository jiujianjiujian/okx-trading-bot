"""市场数据服务测试 — 会话判断 + 黑天鹅逻辑"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from src.services.market_service import MarketService
from src.core.enums import SessionType


@pytest.fixture
def market_svc():
    return MarketService(MagicMock())


class TestSessionDetection:
    # May 31 2026 = Sunday (weekday 6)
    # Jun 1 = Monday, Jun 2 = Tuesday, Jun 3 = Wed, Jun 4 = Thu

    def test_overlap_session(self):
        """周三 UTC 12:00 = 北京 20:00 → 重叠"""
        with patch("src.services.market_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
            assert MarketService.current_session() == SessionType.OVERLAP

    def test_asia_session(self):
        """周三 UTC 2:00 = 北京 10:00 → 亚盘"""
        with patch("src.services.market_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 3, 2, 0, tzinfo=timezone.utc)
            assert MarketService.current_session() == SessionType.ASIA

    def test_weekend_session(self):
        """周六 UTC 2:00 → 周末"""
        with patch("src.services.market_service.datetime") as mock_dt:
            # May 30 2026 = Saturday
            mock_dt.now.return_value = datetime(2026, 5, 30, 2, 0, tzinfo=timezone.utc)
            assert MarketService.current_session() == SessionType.WEEKEND

    def test_eu_session(self):
        """周三 UTC 8:00 = 北京 16:00 → 欧盘"""
        with patch("src.services.market_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 3, 8, 0, tzinfo=timezone.utc)
            assert MarketService.current_session() == SessionType.EU

    def test_us_session(self):
        """周三 UTC 15:00 = 北京 23:00 → 美盘"""
        with patch("src.services.market_service.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 3, 15, 0, tzinfo=timezone.utc)
            assert MarketService.current_session() == SessionType.US


class TestBlackswan:
    def test_rate_limit_not_blackswan(self, market_svc):
        bybit = market_svc._bybit
        bybit.get_klines.side_effect = RuntimeError("Rate Limit 10006")
        assert market_svc.check_blackswan() is False

    def test_network_error_not_blackswan(self, market_svc):
        bybit = market_svc._bybit
        bybit.get_klines.side_effect = RuntimeError("Connection timeout")
        assert market_svc.check_blackswan() is False

    def test_insufficient_data(self, market_svc):
        bybit = market_svc._bybit
        bybit.get_klines.return_value = []
        assert market_svc.check_blackswan() is False
