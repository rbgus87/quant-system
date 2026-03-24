# gui/widgets/portfolio_view.py
"""포트폴리오 보유 현황 테이블"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
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
            from trading.kiwoom_api import KiwoomRestClient

            api = KiwoomRestClient()
            balance = api.get_balance()
            self.finished.emit(balance)
        except Exception as e:
            self.error.emit(str(e))


class PortfolioView(QWidget):
    """보유 종목 테이블 위젯"""

    HEADERS = ["종목코드", "종목명", "수량", "매수가", "현재가", "평가금액", "수익률"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_BalanceWorker] = None
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

            display_values = [
                (ticker, None),
                (name, None),
                (f"{qty:,}", float(qty)),
                (f"{avg_price:,.0f}", float(avg_price)),
                (f"{current_price:,.0f}", float(current_price)),
                (f"{eval_amount:,.0f}", float(eval_amount)),
                (f"{profit_rate:+.2f}%", float(profit_rate)),
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
                self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)

        # 총 수익률 계산
        total_rate = (total_profit / total_buy * 100) if total_buy else 0
        self._total_label.setText(
            f"총 평가: {total:,.0f}원 | 손익: {total_profit:+,.0f}원 ({total_rate:+.2f}%) | "
            f"예수금: {cash:,.0f}원 | {len(holdings)}종목"
        )

    def _on_balance_error(self, error_msg: str) -> None:
        """잔고 조회 실패"""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._total_label.setText(f"조회 실패: {error_msg}")
        self._table.setRowCount(0)
        logger.warning(f"잔고 조회 실패: {error_msg}")
