"""포트폴리오 요약 카드 (좌측 패널)

데이터 소스:
- 총 평가/예수금/총 수익률: PortfolioView의 balance_updated 시그널 수신
- 당일 손익: peak_value 파일의 prev_value 와 비교
- 다음 리밸런싱 D-day: config.calendar + settings.portfolio.rebalance_frequency
- 시장 국면: strategy.market_regime.MarketRegimeFilter (1시간 단위 백그라운드 갱신)
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from gui.themes import accent_palette

logger = logging.getLogger(__name__)

_REGIME_REFRESH_MS = 60 * 60 * 1000  # 1시간
_DDAY_REFRESH_MS = 60 * 60 * 1000


def _peak_value_path() -> str:
    """peak_value_{paper|live}.json 경로"""
    is_paper = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
    mode = "paper" if is_paper else "live"
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", f"peak_value_{mode}.json")


def _load_prev_value() -> float:
    try:
        data = json.loads(Path(_peak_value_path()).read_text(encoding="utf-8"))
        return float(data.get("prev_value", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0.0


class _RegimeWorker(QThread):
    """시장 국면 백그라운드 조회 (collector + 200일 데이터 필요해 무거움 → 1시간 1회)"""

    finished = pyqtSignal(str, float)  # regime_label, invest_ratio
    error = pyqtSignal(str)

    def run(self) -> None:
        try:
            from gui.services import get_collector
            from strategy.market_regime import MarketRegimeFilter

            mrf = MarketRegimeFilter(get_collector())
            today = datetime.now().strftime("%Y%m%d")
            ratio = mrf.get_invest_ratio(today)

            # 비중 → 라벨 매핑 (MarketRegimeFilter 내부 임계값 추정)
            if ratio >= 0.95:
                label = "🟢 상승장"
            elif ratio >= 0.55:
                label = "🟡 중립"
            else:
                label = "🔴 하락장"
            self.finished.emit(label, ratio)
        except Exception as e:
            logger.warning("시장 국면 조회 실패: %s", e)
            self.error.emit(str(e))


def _next_rebalance_date(freq: str) -> Optional[datetime]:
    """다음 리밸런싱 영업일 계산. 실패 시 None"""
    try:
        from config.calendar import get_krx_month_end_sessions

        now = datetime.now()
        end = now + timedelta(days=200)
        month_ends = get_krx_month_end_sessions(
            now.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )
        for dt in month_ends:
            if dt.date() < now.date():
                continue
            if freq == "quarterly" and dt.month not in (3, 6, 9, 12):
                continue
            return dt
    except Exception as e:
        logger.debug("다음 리밸런싱 계산 실패: %s", e)
    return None


class SummaryCard(QGroupBox):
    """포트폴리오 요약 카드 (좌측 패널)"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("📊 오늘의 요약", parent)
        self._regime_worker: Optional[_RegimeWorker] = None
        self._is_dark = True
        # 마지막 잔고 캐시 (set_dark_mode 후 색상 재계산용)
        self._last_balance: Optional[dict] = None
        self._setup_ui()
        self._setup_timers()
        self._refresh_dday()
        self._refresh_regime()

    def set_dark_mode(self, is_dark: bool) -> None:
        """테마 변경 시 색상 재적용 (MainWindow._apply_theme에서 호출)"""
        self._is_dark = is_dark
        if self._last_balance is not None:
            self.update_balance(self._last_balance)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(4)

        form = QFormLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)

        bold_font = QFont()
        bold_font.setBold(True)

        def _make_value() -> QLabel:
            lbl = QLabel("-")
            lbl.setFont(bold_font)
            return lbl

        self._total_lbl = _make_value()
        self._daily_pl_lbl = _make_value()
        self._total_return_lbl = _make_value()
        self._cash_lbl = _make_value()
        self._next_reb_lbl = _make_value()
        self._regime_lbl = _make_value()

        form.addRow(QLabel("총 평가:"), self._total_lbl)
        form.addRow(QLabel("당일 손익:"), self._daily_pl_lbl)
        form.addRow(QLabel("총 수익률:"), self._total_return_lbl)
        form.addRow(QLabel("예수금:"), self._cash_lbl)
        form.addRow(QLabel("다음 리밸런싱:"), self._next_reb_lbl)
        form.addRow(QLabel("시장 국면:"), self._regime_lbl)

        layout.addLayout(form)

    def _setup_timers(self) -> None:
        self._regime_timer = QTimer(self)
        self._regime_timer.timeout.connect(self._refresh_regime)
        self._regime_timer.start(_REGIME_REFRESH_MS)

        self._dday_timer = QTimer(self)
        self._dday_timer.timeout.connect(self._refresh_dday)
        self._dday_timer.start(_DDAY_REFRESH_MS)

    # ── 잔고 시그널 수신 ──

    def update_balance(self, balance: dict) -> None:
        """PortfolioView.balance_updated 시그널 수신 핸들러

        손익/수익률 분모는 키움 API 응답의 ``purchase_amount`` (총매입금액)을
        그대로 사용한다. PortfolioView 상단 바와 동일한 소스/분모를 사용하여
        화면에 두 다른 숫자가 표시되는 문제를 차단한다.
        """
        if not isinstance(balance, dict):
            return
        self._last_balance = balance
        palette = accent_palette(self._is_dark)
        total = balance.get("total_eval_amount", 0)
        cash = balance.get("cash", 0)
        total_profit = balance.get("total_profit", 0)
        # 신규 키 우선 사용. 구버전 잔고 dict 호환 위해 평가-손익 역산 폴백.
        purchase_amount = balance.get("purchase_amount")
        if not purchase_amount:
            purchase_amount = total - total_profit if total_profit else total

        self._total_lbl.setText(f"{total:,.0f}원")
        self._cash_lbl.setText(f"{cash:,.0f}원")

        if purchase_amount > 0:
            rate = total_profit / purchase_amount * 100
            color = palette["profit"] if rate >= 0 else palette["loss"]
            self._total_return_lbl.setTextFormat(Qt.TextFormat.RichText)
            self._total_return_lbl.setText(
                f"<span style='color:{color};'>{rate:+.2f}% ({total_profit:+,.0f}원)</span>"
            )
        else:
            self._total_return_lbl.setText("-")

        # 당일 손익 = 현재 평가 - 전일 평가 (peak_value_*.json)
        prev = _load_prev_value()
        if prev > 0 and total > 0:
            daily = total - prev
            color = palette["profit"] if daily >= 0 else palette["loss"]
            self._daily_pl_lbl.setTextFormat(Qt.TextFormat.RichText)
            self._daily_pl_lbl.setText(
                f"<span style='color:{color};'>{daily:+,.0f}원</span>"
            )
        else:
            self._daily_pl_lbl.setText("-")

    # ── 다음 리밸런싱 D-day ──

    def _refresh_dday(self) -> None:
        try:
            from config.settings import settings

            freq = settings.portfolio.rebalance_frequency
        except Exception:
            freq = "quarterly"

        nxt = _next_rebalance_date(freq)
        if nxt is None:
            self._next_reb_lbl.setText("계산 불가")
            return
        days_left = (nxt.date() - datetime.now().date()).days
        date_str = nxt.strftime("%-m/%-d") if os.name != "nt" else nxt.strftime("%#m/%#d")
        self._next_reb_lbl.setText(f"{date_str} (D-{days_left})")

    # ── 시장 국면 ──

    def _refresh_regime(self) -> None:
        if self._regime_worker is not None and self._regime_worker.isRunning():
            return
        self._regime_lbl.setText("조회 중...")
        self._regime_worker = _RegimeWorker(self)
        self._regime_worker.finished.connect(self._on_regime_loaded)
        self._regime_worker.error.connect(self._on_regime_error)
        self._regime_worker.start()

    def _on_regime_loaded(self, label: str, _ratio: float) -> None:
        self._regime_lbl.setText(label)

    def _on_regime_error(self, _msg: str) -> None:
        self._regime_lbl.setText("조회 실패")
