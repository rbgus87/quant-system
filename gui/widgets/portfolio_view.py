# gui/widgets/portfolio_view.py
"""포트폴리오 보유 현황 테이블"""

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _NumericTableItem(QTableWidgetItem):
    """숫자 기준 정렬을 위한 QTableWidgetItem 서브클래스"""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        my_val = self.data(Qt.ItemDataRole.UserRole)
        other_val = other.data(Qt.ItemDataRole.UserRole)
        if my_val is not None and other_val is not None:
            return float(my_val) < float(other_val)
        return super().__lt__(other)


class _BalanceWorker(QThread):
    """잔고 조회를 백그라운드에서 실행"""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self) -> None:
        try:
            from gui.services import get_api

            balance = get_api().get_balance()
            self._enrich_change_rate(balance)
            self.finished.emit(balance)
        except Exception as e:
            self.error.emit(str(e))

    @staticmethod
    def _enrich_change_rate(balance: dict) -> None:
        """잔고 응답이 당일 등락률을 포함하지 않으면 DB 전일 종가로 보강한다.

        키움 잔고 API 응답에 등락률 필드가 누락된 종목에 대해서만 1회 DB 쿼리로
        직전 daily_price.close 를 조회해 (현재가 - 전일종가)/전일종가 × 100 을 채운다.
        DB 조회 실패는 무시(change_rate=None 유지).
        """
        holdings = balance.get("holdings") or []
        tickers_to_fetch = [
            h["ticker"]
            for h in holdings
            if h.get("change_rate") is None and h.get("ticker") and h.get("current_price")
        ]
        if not tickers_to_fetch:
            return
        try:
            from sqlalchemy import bindparam, text

            from gui.services import get_storage

            storage = get_storage()
            # 보유 종목별 최신 daily_price.close — IN 쿼리 1회
            stmt = text(
                "SELECT dp.ticker, dp.close FROM daily_price dp "
                "JOIN (SELECT ticker, MAX(date) AS mx FROM daily_price "
                "      WHERE ticker IN :tickers GROUP BY ticker) g "
                "ON dp.ticker = g.ticker AND dp.date = g.mx"
            ).bindparams(bindparam("tickers", expanding=True))
            with storage.engine.connect() as conn:
                rows = conn.execute(stmt, {"tickers": tickers_to_fetch}).fetchall()
            prev_close_by_ticker = {r[0]: float(r[1]) for r in rows if r[1]}
        except Exception as e:
            logger.warning("당일 등락률 DB 보강 실패: %s", e)
            return

        for h in holdings:
            if h.get("change_rate") is not None:
                continue
            prev_close = prev_close_by_ticker.get(h.get("ticker"))
            cur = h.get("current_price")
            if prev_close and cur and prev_close > 0:
                h["change_rate"] = (cur - prev_close) / prev_close * 100.0


class _TelegramReportWorker(QThread):
    """잔고 조회 + 텔레그램 리포트 발송을 백그라운드에서 실행"""

    finished = pyqtSignal(bool)  # True=성공
    error = pyqtSignal(str)

    def run(self) -> None:
        try:
            from notify.telegram import TelegramNotifier

            from gui.services import get_api

            balance = get_api().get_balance()
            notifier = TelegramNotifier()
            ok = notifier.send_detailed_daily_report(balance)
            self.finished.emit(ok)
        except Exception as e:
            self.error.emit(str(e))


class PortfolioView(QWidget):
    """보유 종목 테이블 위젯"""

    HEADERS = [
        "종목코드", "종목명", "수량", "매수가", "현재가",
        "평가금액", "수익률", "당일등락",
    ]

    # 잔고 갱신 완료 시 외부 위젯(SummaryCard 등)에 잔고 dict 전파
    balance_updated = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_BalanceWorker] = None
        self._report_worker: Optional[_TelegramReportWorker] = None
        # 잔고 캐시 (행 더블클릭 시 holding dict 조회용)
        self._holdings_cache: list[dict] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("보유 종목")
        group_layout = QVBoxLayout(group)

        # 요약 행
        summary_row = QHBoxLayout()
        self._total_label = QLabel("총 평가: -")
        self._total_label.setStyleSheet("font-weight: bold;")
        summary_row.addWidget(self._total_label)
        summary_row.addStretch()

        self._refresh_btn = QPushButton("새로고침")
        self._refresh_btn.clicked.connect(self.refresh)
        summary_row.addWidget(self._refresh_btn)

        self._report_btn = QPushButton("텔레그램 리포트")
        self._report_btn.setToolTip("현재 잔고 기준으로 텔레그램 일별 리포트 발송")
        self._report_btn.clicked.connect(self._send_telegram_report)
        summary_row.addWidget(self._report_btn)

        group_layout.addLayout(summary_row)

        # 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.setSortingEnabled(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.setToolTip("종목 행을 더블클릭하면 상세 정보를 볼 수 있습니다")
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        # 기본 정렬: 종목코드 오름차순 (헤더 클릭 시 ▲▼ 화살표 표시됨)
        self._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        group_layout.addWidget(self._table)

        layout.addWidget(group)

    def refresh(self) -> None:
        """키움 API에서 잔고 조회 (백그라운드 스레드)"""
        if self._worker and self._worker.isRunning():
            return

        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("조회 중...")
        self._total_label.setText("잔고 조회 중...")

        self._worker = _BalanceWorker()
        self._worker.finished.connect(self._on_balance_loaded)
        self._worker.error.connect(self._on_balance_error)
        self._worker.start()

    def _on_balance_loaded(self, balance: dict) -> None:
        """잔고 조회 완료"""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")

        holdings = balance.get("holdings", [])
        cash = balance.get("cash", 0)
        total = balance.get("total_eval_amount", 0)
        self._holdings_cache = list(holdings)

        # 정렬 일시 비활성화 (행 삽입 중 정렬 방지)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(holdings))

        total_profit = 0
        total_buy = 0

        for row, h in enumerate(holdings):
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            qty = h.get("qty", 0)
            avg_price = h.get("avg_price", 0)
            current_price = h.get("current_price", 0)
            eval_amount = h.get("eval_amount", 0)
            profit_rate = h.get("profit_rate", 0)

            buy_amount = avg_price * qty
            eval_profit = eval_amount - buy_amount
            total_profit += eval_profit
            total_buy += buy_amount

            # 당일 등락률 — 미확보(None) 시 정렬용 0, 표시는 "-"
            change_rate = h.get("change_rate")
            if change_rate is None:
                change_text = "-"
                change_sort = 0.0
            else:
                change_text = f"{change_rate:+.2f}%"
                change_sort = float(change_rate)

            display_values = [
                (ticker, None),
                (name, None),
                (f"{qty:,}", float(qty)),
                (f"{avg_price:,.0f}", float(avg_price)),
                (f"{current_price:,.0f}", float(current_price)),
                (f"{eval_amount:,.0f}", float(eval_amount)),
                (f"{profit_rate:+.2f}%", float(profit_rate)),
                (change_text, change_sort),
            ]

            for col, (text, sort_val) in enumerate(display_values):
                item = _NumericTableItem(text) if sort_val is not None else QTableWidgetItem(text)
                if sort_val is not None:
                    item.setData(Qt.ItemDataRole.UserRole, sort_val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if col >= 2
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                # 수익률 색상 강화
                if col == 6:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    if profit_rate > 0:
                        item.setForeground(QColor("#FF4444"))
                        item.setBackground(QColor(255, 68, 68, 25))
                    elif profit_rate < 0:
                        item.setForeground(QColor("#4DABF7"))
                        item.setBackground(QColor(77, 171, 247, 25))
                # 당일등락 색상 — 한국 컨벤션 (양:빨강, 음:파랑, 보합:회색)
                elif col == 7 and change_rate is not None:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    if change_rate > 0:
                        item.setForeground(QColor("#FF4444"))
                        item.setBackground(QColor(255, 68, 68, 25))
                    elif change_rate < 0:
                        item.setForeground(QColor("#4DABF7"))
                        item.setBackground(QColor(77, 171, 247, 25))
                    else:
                        item.setForeground(QColor("#9E9E9E"))
                self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)

        # 총 수익률 계산
        total_rate = (total_profit / total_buy * 100) if total_buy else 0
        fetched_at = datetime.now().strftime("%H:%M:%S")
        self._total_label.setText(
            f"총 평가: {total:,.0f}원 | 손익: {total_profit:+,.0f}원 ({total_rate:+.2f}%) | "
            f"예수금: {cash:,.0f}원 | {len(holdings)}종목 | 조회: {fetched_at}"
        )

        # 외부 위젯(SummaryCard 등)에 잔고 전파
        self.balance_updated.emit(balance)

    def _on_balance_error(self, error_msg: str) -> None:
        """잔고 조회 실패"""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._total_label.setText(f"조회 실패: {error_msg}")
        self._table.setRowCount(0)
        logger.warning(f"잔고 조회 실패: {error_msg}")

    # ── 텔레그램 리포트 발송 ──

    def _send_telegram_report(self) -> None:
        """텔레그램 일별 리포트 수동 발송"""
        if self._report_worker and self._report_worker.isRunning():
            return

        self._report_btn.setEnabled(False)
        self._report_btn.setText("발송 중...")

        self._report_worker = _TelegramReportWorker()
        self._report_worker.finished.connect(self._on_report_sent)
        self._report_worker.error.connect(self._on_report_error)
        self._report_worker.start()

    def _on_report_sent(self, ok: bool) -> None:
        """리포트 발송 완료"""
        self._report_btn.setEnabled(True)
        if ok:
            self._report_btn.setText("발송 완료!")
            QTimer.singleShot(3000, lambda: self._report_btn.setText("텔레그램 리포트"))
        else:
            self._report_btn.setText("발송 실패")
            QTimer.singleShot(3000, lambda: self._report_btn.setText("텔레그램 리포트"))
        logger.info(f"텔레그램 리포트 발송: {'성공' if ok else '실패'}")

    def _on_report_error(self, error_msg: str) -> None:
        """리포트 발송 오류"""
        self._report_btn.setEnabled(True)
        self._report_btn.setText("발송 오류")
        QTimer.singleShot(3000, lambda: self._report_btn.setText("텔레그램 리포트"))
        logger.error(f"텔레그램 리포트 발송 오류: {error_msg}")

    # ── 종목 상세 ──

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        """행 더블클릭 → 종목 상세 팝업"""
        ticker_item = self._table.item(row, 0)
        if ticker_item is None:
            return
        ticker = ticker_item.text()
        if not ticker:
            return
        # 캐시에서 holding dict 찾기 (정렬된 상태에서도 ticker로 매칭)
        holding = next(
            (h for h in self._holdings_cache if h.get("ticker") == ticker),
            None,
        )
        if holding is None:
            return
        from gui.widgets.stock_detail_dialog import StockDetailDialog

        # MainWindow의 _is_dark를 self.window()로 조회하여 다이얼로그에 전달.
        # QDialog는 부모 setStyleSheet을 자동 상속하지 않을 수 있어 명시 필요.
        is_dark = bool(getattr(self.window(), "_is_dark", True))
        dialog = StockDetailDialog(ticker, holding, parent=self, is_dark=is_dark)
        dialog.exec()
