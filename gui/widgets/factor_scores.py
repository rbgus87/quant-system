# gui/widgets/factor_scores.py
"""팩터 스코어 탭 - 스크리닝 실행 + 결과 테이블 + 종목 상세"""

import logging
from datetime import datetime
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
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

N_DISPLAY = 30  # 상위 N종목 표시


class _NumericItem(QTableWidgetItem):
    """숫자 기준 정렬"""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        my_val = self.data(Qt.ItemDataRole.UserRole)
        other_val = other.data(Qt.ItemDataRole.UserRole)
        if my_val is not None and other_val is not None:
            return float(my_val) < float(other_val)
        return super().__lt__(other)


class _ScreenWorker(QThread):
    """스크리닝 백그라운드 실행"""

    finished = pyqtSignal(object, list)  # (DataFrame, [current_holdings])
    error = pyqtSignal(str)

    def __init__(self, date_str: str) -> None:
        super().__init__()
        self._date_str = date_str

    def run(self) -> None:
        try:
            from strategy.screener import MultiFactorScreener

            screener = MultiFactorScreener()
            result = screener.screen(self._date_str, n_stocks=N_DISPLAY)

            # 현재 보유 종목 조회 (실패해도 무시)
            holdings: list[str] = []
            try:
                from trading.kiwoom_api import KiwoomRestClient

                api = KiwoomRestClient()
                balance = api.get_balance()
                holdings = [
                    h["ticker"]
                    for h in balance.get("holdings", [])
                    if h.get("qty", 0) > 0
                ]
            except Exception:
                pass

            self.finished.emit(result, holdings)
        except Exception as e:
            self.error.emit(str(e))


class FactorScores(QWidget):
    """팩터 스코어 뷰어"""

    HEADERS = ["순위", "종목코드", "종목명", "복합", "밸류", "모멘텀", "보유"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_ScreenWorker] = None
        self._result_df: Optional[pd.DataFrame] = None
        self._collector = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 상단: 컨트롤
        ctrl_row = QHBoxLayout()
        self._date_label = QLabel("")
        ctrl_row.addWidget(self._date_label)
        ctrl_row.addStretch()

        self._screen_btn = QPushButton("스크리닝 실행")
        self._screen_btn.setToolTip("현재 날짜 기준으로 팩터 스크리닝 (상위 30종목)")
        self._screen_btn.clicked.connect(self.run_screening)
        ctrl_row.addWidget(self._screen_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        ctrl_row.addWidget(self._status_label)
        layout.addLayout(ctrl_row)

        # 스플리터: 상(테이블) / 하(상세)
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 결과 테이블
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
        self._table.currentCellChanged.connect(
            lambda row, _col, _prev_row, _prev_col: self._on_row_selected(row)
        )
        splitter.addWidget(self._table)

        # 하단: 종목 상세
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setFont(QFont("Consolas", 9))
        self._detail_text.setPlaceholderText(
            "'스크리닝 실행' 버튼을 눌러 현재 팩터 스코어를 확인하세요"
        )
        self._detail_text.setMaximumHeight(180)
        splitter.addWidget(self._detail_text)

        splitter.setSizes([400, 150])
        layout.addWidget(splitter)

    def run_screening(self) -> None:
        """스크리닝 실행 (백그라운드)"""
        if self._worker and self._worker.isRunning():
            return

        date_str = datetime.now().strftime("%Y%m%d")
        self._date_label.setText(f"기준일: {date_str}")
        self._screen_btn.setEnabled(False)
        self._screen_btn.setText("스크리닝 중...")
        self._status_label.setText("팩터 계산 + 유니버스 필터 진행 중...")

        self._worker = _ScreenWorker(date_str)
        self._worker.finished.connect(self._on_screen_done)
        self._worker.error.connect(self._on_screen_error)
        self._worker.start()

    def _on_screen_done(self, df: pd.DataFrame, holdings: list[str]) -> None:
        self._screen_btn.setEnabled(True)
        self._screen_btn.setText("스크리닝 실행")

        if df.empty:
            self._status_label.setText("스크리닝 결과 없음")
            self._table.setRowCount(0)
            return

        self._result_df = df
        self._status_label.setText(f"{len(df)}종목 완료")

        # 종목명 로드
        try:
            from data.collector import KRXDataCollector
            self._collector = KRXDataCollector()
        except Exception:
            self._collector = None

        # 테이블 채우기
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(df))

        for row, (ticker, data) in enumerate(df.iterrows()):
            composite = data.get("composite_score", 0)
            value = data.get("value_score", 0)
            momentum = data.get("momentum_score", 0)
            is_held = ticker in holdings

            name = ""
            if self._collector:
                name = self._collector.get_ticker_name(str(ticker)) or ""

            values = [
                (str(row + 1), float(row + 1)),
                (str(ticker), None),
                (name, None),
                (f"{composite:.1f}", float(composite)),
                (f"{value:.1f}" if pd.notna(value) else "-", float(value) if pd.notna(value) else 0),
                (f"{momentum:.1f}" if pd.notna(momentum) else "-", float(momentum) if pd.notna(momentum) else 0),
                ("*" if is_held else "", 1.0 if is_held else 0.0),
            ]

            for col, (text, sort_val) in enumerate(values):
                if sort_val is not None:
                    item = _NumericItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, sort_val)
                else:
                    item = QTableWidgetItem(text)

                align = (
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    if col in (0, 6)
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if col >= 3
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                item.setTextAlignment(align)

                # 보유 종목 하이라이트
                if is_held:
                    item.setBackground(QColor(77, 171, 247, 30))

                self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)

    def _on_screen_error(self, msg: str) -> None:
        self._screen_btn.setEnabled(True)
        self._screen_btn.setText("스크리닝 실행")
        self._status_label.setText(f"실패: {msg}")
        logger.error(f"스크리닝 실패: {msg}")

    def _on_row_selected(self, row: int) -> None:
        """종목 클릭 시 원시 지표 상세 표시"""
        if self._result_df is None or row < 0 or row >= len(self._result_df):
            return

        ticker = str(self._result_df.index[row])
        data = self._result_df.iloc[row]

        name = ""
        if self._collector:
            name = self._collector.get_ticker_name(ticker) or ""

        lines = [f"=== {ticker} {name} ===\n"]

        # 팩터 스코어
        for col_name, label in [
            ("composite_score", "복합 스코어"),
            ("value_score", "밸류 스코어"),
            ("momentum_score", "모멘텀 스코어"),
            ("quality_score", "퀄리티 스코어"),
        ]:
            if col_name in data.index:
                val = data[col_name]
                lines.append(f"  {label}: {val:.2f}" if pd.notna(val) else f"  {label}: -")

        lines.append("")

        # 가중치 정보
        if "weight" in data.index:
            lines.append(f"  포트폴리오 비중: {data['weight']:.2%}")

        # 원시 지표 (있으면)
        raw_cols = {
            "PBR": "PBR", "PCR": "PCR", "DIV": "배당수익률",
            "per": "PER", "pbr": "PBR", "div_yield": "배당수익률",
        }
        raw_items = []
        for col, label in raw_cols.items():
            if col in data.index and pd.notna(data[col]):
                raw_items.append(f"{label}={data[col]:.2f}")
        if raw_items:
            lines.append(f"  원시 지표: {' | '.join(raw_items)}")

        self._detail_text.setPlainText("\n".join(lines))
