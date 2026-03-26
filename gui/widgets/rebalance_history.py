# gui/widgets/rebalance_history.py
"""리밸런싱 이력 탭 - 거래 로그 기반 리밸런싱 요약 + 상세"""

import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _TradeLoadWorker(QThread):
    """거래 이력 DB 조회 (백그라운드)"""

    finished = pyqtSignal(object)  # pd.DataFrame
    error = pyqtSignal(str)

    def run(self) -> None:
        try:
            from data.storage import DataStorage

            storage = DataStorage()
            df = storage.load_trades()
            self.finished.emit(df)
        except Exception as e:
            self.error.emit(str(e))


class RebalanceHistory(QWidget):
    """리밸런싱 이력 뷰어"""

    SUMMARY_HEADERS = ["날짜", "매도", "매수", "턴오버", "비고"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_TradeLoadWorker] = None
        self._trades_df: Optional[pd.DataFrame] = None
        self._rebal_groups: list[dict] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 상단 컨트롤
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("리밸런싱 이력"))
        ctrl_row.addStretch()
        self._refresh_btn = QPushButton("새로고침")
        self._refresh_btn.clicked.connect(self.refresh)
        ctrl_row.addWidget(self._refresh_btn)
        layout.addLayout(ctrl_row)

        # 스플리터: 상(요약 테이블) / 하(상세)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 요약 테이블
        self._summary_table = QTableWidget()
        self._summary_table.setColumnCount(len(self.SUMMARY_HEADERS))
        self._summary_table.setHorizontalHeaderLabels(self.SUMMARY_HEADERS)
        self._summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._summary_table.setAlternatingRowColors(True)
        self._summary_table.currentCellChanged.connect(
            lambda row, _col, _prev_row, _prev_col: self._on_row_selected(row)
        )
        splitter.addWidget(self._summary_table)

        # 상세 영역
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        from PyQt6.QtGui import QFont
        self._detail_text.setFont(QFont("Consolas", 9))
        self._detail_text.setPlaceholderText(
            "리밸런싱 이력 없음. 스케줄러 실행 또는 백테스트 후 확인 가능"
        )
        splitter.addWidget(self._detail_text)

        splitter.setSizes([300, 200])
        layout.addWidget(splitter)

    def refresh(self) -> None:
        """DB에서 거래 이력 로드"""
        if self._worker and self._worker.isRunning():
            return

        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("조회 중...")

        self._worker = _TradeLoadWorker()
        self._worker.finished.connect(self._on_trades_loaded)
        self._worker.error.connect(self._on_load_error)
        self._worker.start()

    def _on_trades_loaded(self, df: pd.DataFrame) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")

        if df.empty:
            self._summary_table.setRowCount(0)
            self._detail_text.setPlainText(
                "리밸런싱 이력 없음. 스케줄러 실행 또는 백테스트 후 확인 가능"
            )
            return

        self._trades_df = df
        self._build_rebal_summary(df)

    def _on_load_error(self, msg: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._detail_text.setPlainText(f"조회 실패: {msg}")

    def _build_rebal_summary(self, df: pd.DataFrame) -> None:
        """거래 로그를 날짜별로 그룹핑하여 리밸런싱 요약 생성"""
        if "trade_date" not in df.columns:
            return

        df["trade_date"] = pd.to_datetime(df["trade_date"])
        grouped = df.groupby(df["trade_date"].dt.date)

        self._rebal_groups = []
        for dt, group in sorted(grouped, reverse=True):
            sells = group[group["side"] == "SELL"]
            buys = group[group["side"] == "BUY"]
            n_sells = len(sells)
            n_buys = len(buys)
            total_traded = n_sells + n_buys
            turnover = f"{total_traded}건"

            self._rebal_groups.append({
                "date": dt,
                "n_sells": n_sells,
                "n_buys": n_buys,
                "turnover": turnover,
                "sells": sells,
                "buys": buys,
            })

        self._summary_table.setSortingEnabled(False)
        self._summary_table.setRowCount(len(self._rebal_groups))
        for row, g in enumerate(self._rebal_groups):
            items = [
                str(g["date"]),
                f"{g['n_sells']}종목",
                f"{g['n_buys']}종목",
                g["turnover"],
                "",
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                )
                self._summary_table.setItem(row, col, item)

        self._summary_table.setSortingEnabled(True)
        if self._rebal_groups:
            self._summary_table.selectRow(0)

    def _on_row_selected(self, row: int) -> None:
        """리밸런싱 행 클릭 시 상세 표시"""
        if row < 0 or row >= len(self._rebal_groups):
            return

        g = self._rebal_groups[row]
        lines = [f"=== {g['date']} 리밸런싱 상세 ===\n"]

        if not g["sells"].empty:
            lines.append(f"[매도] {g['n_sells']}종목")
            for _, r in g["sells"].iterrows():
                ticker = r.get("ticker", "")
                qty = r.get("quantity", 0)
                price = r.get("price", 0)
                amount = r.get("amount", 0)
                lines.append(f"  {ticker}  {qty:,}주  @{price:,.0f}  = {amount:,.0f}원")
            lines.append("")

        if not g["buys"].empty:
            lines.append(f"[매수] {g['n_buys']}종목")
            for _, r in g["buys"].iterrows():
                ticker = r.get("ticker", "")
                qty = r.get("quantity", 0)
                price = r.get("price", 0)
                amount = r.get("amount", 0)
                lines.append(f"  {ticker}  {qty:,}주  @{price:,.0f}  = {amount:,.0f}원")

        self._detail_text.setPlainText("\n".join(lines))
