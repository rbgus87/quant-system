# gui/widgets/portfolio_view.py
"""포트폴리오 보유 현황 테이블"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
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


class PortfolioView(QWidget):
    """보유 종목 테이블 위젯"""

    HEADERS = ["종목코드", "종목명", "수량", "매수가", "현재가", "평가금액", "수익률"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
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

        refresh_btn = QPushButton("새로고침")
        refresh_btn.clicked.connect(self.refresh)
        summary_row.addWidget(refresh_btn)
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
        group_layout.addWidget(self._table)

        layout.addWidget(group)

    def refresh(self) -> None:
        """키움 API에서 잔고 조회 후 테이블 갱신"""
        try:
            from trading.kiwoom_api import KiwoomRestClient

            api = KiwoomRestClient()
            balance = api.get_balance()

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
                pnl_pct = h.get("pnl_pct", 0)

                items = [
                    ticker,
                    name,
                    f"{qty:,}",
                    f"{avg_price:,.0f}",
                    f"{current_price:,.0f}",
                    f"{eval_amount:,.0f}",
                    f"{pnl_pct:+.2f}%",
                ]

                for col, val in enumerate(items):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                        if col >= 2
                        else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    )
                    # 수익률 색상
                    if col == 6:
                        if pnl_pct > 0:
                            item.setForeground(Qt.GlobalColor.red)
                        elif pnl_pct < 0:
                            item.setForeground(Qt.GlobalColor.blue)
                    self._table.setItem(row, col, item)

            self._total_label.setText(
                f"총 평가: {total:,.0f}원 | 예수금: {cash:,.0f}원 | "
                f"종목 수: {len(holdings)}"
            )

        except Exception as e:
            logger.warning(f"잔고 조회 실패: {e}")
            self._total_label.setText(f"조회 실패: {e}")
            self._table.setRowCount(0)

    def update_holdings(self, holdings: list[dict], total: float, cash: float) -> None:
        """외부에서 직접 데이터를 전달하여 테이블 갱신"""
        self._table.setRowCount(len(holdings))

        for row, h in enumerate(holdings):
            items = [
                h.get("ticker", ""),
                h.get("name", ""),
                f"{h.get('qty', 0):,}",
                f"{h.get('avg_price', 0):,.0f}",
                f"{h.get('current_price', 0):,.0f}",
                f"{h.get('eval_amount', 0):,.0f}",
                f"{h.get('pnl_pct', 0):+.2f}%",
            ]
            for col, val in enumerate(items):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if col >= 2
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._table.setItem(row, col, item)

        self._total_label.setText(
            f"총 평가: {total:,.0f}원 | 예수금: {cash:,.0f}원 | "
            f"종목 수: {len(holdings)}"
        )
