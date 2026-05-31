"""通知适配器 — CompositeNotifier + ConsoleNotifier"""

from ..core.models import TradeSignal, ScalpDecision, PnLRecord
from ..services.report_service import ReportService

report = ReportService()


class ConsoleNotifier:
    """开发/调试用 — 打印到 stdout"""

    def send(self, text: str, parse_mode: str = "Markdown") -> None:
        print(f"[NOTIFY] {text[:300]}")

    def notify_signal(self, signal: TradeSignal) -> None:
        print(f"[SIGNAL] {signal.bybit_symbol} {signal.direction} @ {signal.price}")

    def notify_scalp_decision(self, decision: ScalpDecision) -> None:
        print(report.scalp_decision_preview(decision))

    def notify_trade_open(self, decision: ScalpDecision, signal_id: int) -> None:
        print(report.trade_opened(decision, signal_id))

    def notify_trade_close(self, rec: PnLRecord) -> None:
        print(report.trade_closed(rec))

    def notify_error(self, title: str, detail: str) -> None:
        print(f"[ERROR] {title}: {detail[:200]}")

    def notify_daily_stats(self, stats) -> None:
        print(report.daily_stats(stats))


class CompositeNotifier:
    """聚合多个 Notifier 实现, 广播到所有通道"""

    def __init__(self, notifiers: list | None = None):
        self._notifiers: list = list(notifiers) if notifiers else []

    def add(self, n) -> None:
        self._notifiers.append(n)

    def send(self, text: str, parse_mode: str = "Markdown") -> None:
        for n in self._notifiers:
            try:
                n.send(text, parse_mode)
            except Exception:
                pass

    def notify_signal(self, signal: TradeSignal) -> None:
        for n in self._notifiers:
            try:
                n.notify_signal(signal)
            except Exception:
                pass

    def notify_scalp_decision(self, decision: ScalpDecision) -> None:
        for n in self._notifiers:
            try:
                n.notify_scalp_decision(decision)
            except Exception:
                pass

    def notify_trade_open(self, decision: ScalpDecision, signal_id: int) -> None:
        for n in self._notifiers:
            try:
                n.notify_trade_open(decision, signal_id)
            except Exception:
                pass

    def notify_trade_close(self, rec: PnLRecord) -> None:
        for n in self._notifiers:
            try:
                n.notify_trade_close(rec)
            except Exception:
                pass

    def notify_error(self, title: str, detail: str) -> None:
        for n in self._notifiers:
            try:
                n.notify_error(title, detail)
            except Exception:
                pass

    def notify_daily_stats(self, stats) -> None:
        for n in self._notifiers:
            try:
                n.notify_daily_stats(stats)
            except Exception:
                pass

    @property
    def primary(self):
        return self._notifiers[0] if self._notifiers else ConsoleNotifier()
