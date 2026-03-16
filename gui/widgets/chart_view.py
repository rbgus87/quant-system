# gui/widgets/chart_view.py
"""일별/누적 수익률 차트 (matplotlib embed)"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class ChartView(QWidget):
    """수익률 차트 위젯 (matplotlib 캔버스 임베드)"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._canvas = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("수익률 차트")
        group_layout = QVBoxLayout(group)

        # 기간 선택 + 새로고침
        ctrl_row = QHBoxLayout()
        self._period_combo = QComboBox()
        self._period_combo.addItems(["1주", "1개월", "3개월", "6개월", "전체"])
        self._period_combo.setCurrentIndex(1)
        ctrl_row.addWidget(self._period_combo)

        refresh_btn = QPushButton("차트 갱신")
        refresh_btn.clicked.connect(self.refresh)
        ctrl_row.addWidget(refresh_btn)
        ctrl_row.addStretch()
        group_layout.addLayout(ctrl_row)

        # matplotlib 캔버스 영역
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self._figure = Figure(figsize=(8, 4), dpi=100)
            self._figure.set_facecolor("#FAFAFA")
            self._canvas = FigureCanvasQTAgg(self._figure)
            group_layout.addWidget(self._canvas)
        except ImportError:
            from PyQt6.QtWidgets import QLabel
            group_layout.addWidget(QLabel("matplotlib 미설치 — pip install matplotlib"))
            logger.warning("matplotlib 미설치, 차트 비활성화")

        layout.addWidget(group)

    def refresh(self) -> None:
        """DB에서 Trade 데이터를 조회하여 차트 그리기"""
        if not self._canvas:
            return

        period_text = self._period_combo.currentText()
        period_days = {"1주": 7, "1개월": 30, "3개월": 90, "6개월": 180, "전체": 3650}
        days = period_days.get(period_text, 30)

        try:
            import pandas as pd
            from data.storage import DataStorage

            ds = DataStorage()
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days)
            trades = ds.load_trades(start_date=start_date, end_date=end_date)

            self._figure.clear()
            ax = self._figure.add_subplot(111)

            if trades.empty:
                ax.text(0.5, 0.5, "거래 데이터 없음",
                        ha="center", va="center", fontsize=14, color="gray",
                        transform=ax.transAxes)
                ax.set_facecolor("#FAFAFA")
            else:
                # 일별 매수/매도 금액 집계
                trades["trade_date"] = pd.to_datetime(trades["trade_date"])
                daily = trades.groupby(["trade_date", "side"])["amount"].sum().unstack(fill_value=0)

                if "BUY" in daily.columns:
                    ax.bar(daily.index, daily["BUY"], label="매수", color="#FF6B6B", alpha=0.7, width=0.8)
                if "SELL" in daily.columns:
                    ax.bar(daily.index, -daily["SELL"], label="매도", color="#4DABF7", alpha=0.7, width=0.8)

                ax.axhline(y=0, color="gray", linewidth=0.5)
                ax.legend(fontsize=9)
                ax.set_ylabel("금액 (원)")
                ax.set_facecolor("#FAFAFA")

                # x축 날짜 포맷
                import matplotlib.dates as mdates
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
                self._figure.autofmt_xdate()

            ax.set_title(f"거래 내역 ({period_text})", fontsize=11)
            self._figure.tight_layout()
            self._canvas.draw()

        except Exception as e:
            logger.warning(f"차트 갱신 실패: {e}")
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            ax.text(0.5, 0.5, f"차트 로드 실패:\n{e}",
                    ha="center", va="center", fontsize=10, color="red",
                    transform=ax.transAxes)
            self._canvas.draw()
