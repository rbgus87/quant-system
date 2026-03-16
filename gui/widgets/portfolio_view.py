# gui/widgets/portfolio_view.py
"""포트폴리오 보유 현황 테이블"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
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
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget { alternate-background-color: #F5F5F5; }"
        )
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

        self._table.setRowCount(len(holdings))

        for row, h in enumerate(holdings):
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            qty = h.get("qty", 0)
            avg_price = h.get("avg_price", 0)
            current_price = h.get("current_price", 0)
            eval_amount = h.get("eval_amount", 0)
            profit_rate = h.get("profit_rate", 0)

            items = [
                ticker,
                name,
                f"{qty:,}",
                f"{avg_price:,.0f}",
                f"{current_price:,.0f}",
                f"{eval_amount:,.0f}",
                f"{profit_rate:+.2f}%",
            ]

            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if col >= 2
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                # 수익률 색상 (한국 관례: 상승=빨강, 하락=파랑)
                if col == 6:
                    from PyQt6.QtGui import QColor, QFont
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    if profit_rate > 0:
                        item.setForeground(QColor("#FF3333"))  # 밝은 빨강
                    elif profit_rate < 0:
                        item.setForeground(QColor("#4488FF"))  # 밝은 파랑
                self._table.setItem(row, col, item)

        self._total_label.setText(
            f"총 평가: {total:,.0f}원 | 예수금: {cash:,.0f}원 | "
            f"종목 수: {len(holdings)}"
        )

    def _on_balance_error(self, error_msg: str) -> None:
        """잔고 조회 실패"""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._total_label.setText(f"조회 실패: {error_msg}")
        self._table.setRowCount(0)
        logger.warning(f"잔고 조회 실패: {error_msg}")
