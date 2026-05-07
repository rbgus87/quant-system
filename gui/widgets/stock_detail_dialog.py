"""종목 상세 팝업 — 매수정보/현재상태/팩터점수/최근공시/외부링크

PortfolioView 행 더블클릭 시 호출된다.
"""

import logging
import webbrowser
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_DART_SEARCH_FMT = "https://dart.fss.or.kr/dsab007/main.do?option=corp&textCrpNm={name}"
_NAVER_FMT = "https://finance.naver.com/item/main.nhn?code={ticker}"
_DART_DOC_FMT = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _fmt_currency(v: float) -> str:
    return f"{v:,.0f}원"


def _fmt_pct(v: float) -> str:
    return f"{v:+.2f}%"


class _DetailLoadWorker(QThread):
    """portfolio + factor_score + 최근 공시를 백그라운드에서 조회"""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ticker: str, parent=None) -> None:
        super().__init__(parent)
        self._ticker = ticker

    def run(self) -> None:
        try:
            from sqlalchemy import text

            from data.storage import DataStorage
            from dart_notifier.storage import DartDisclosureStorage

            result: dict = {
                "ticker": self._ticker,
                "portfolio": None,
                "factor": None,
                "disclosures": [],
            }

            storage = DataStorage()
            with storage.engine.connect() as conn:
                # 최신 portfolio (rebalance_date 기준)
                row = conn.execute(
                    text(
                        "SELECT rebalance_date, name, weight, composite_score "
                        "FROM portfolio WHERE ticker = :t "
                        "ORDER BY rebalance_date DESC LIMIT 1"
                    ),
                    {"t": self._ticker},
                ).fetchone()
                if row:
                    rb_date = str(row[0])
                    result["portfolio"] = {
                        "rebalance_date": rb_date,
                        "name": row[1] or self._ticker,
                        "weight": row[2] or 0.0,
                        "composite_score": row[3] or 0.0,
                    }

                    # 동일 rebalance_date의 factor_score
                    fs = conn.execute(
                        text(
                            "SELECT value_score, momentum_score, "
                            "       quality_score, composite_score "
                            "FROM factor_score "
                            "WHERE ticker = :t AND date = :d"
                        ),
                        {"t": self._ticker, "d": rb_date},
                    ).fetchone()
                    if fs:
                        result["factor"] = {
                            "value_score": fs[0] or 0.0,
                            "momentum_score": fs[1] or 0.0,
                            "quality_score": fs[2] or 0.0,
                            "composite_score": fs[3] or 0.0,
                        }

                    # 동일 rebalance_date 내 composite_score 순위
                    rank_row = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM factor_score "
                            "WHERE date = :d AND composite_score > "
                            "(SELECT composite_score FROM factor_score "
                            " WHERE ticker = :t AND date = :d)"
                        ),
                        {"d": rb_date, "t": self._ticker},
                    ).fetchone()
                    if rank_row and result["factor"] is not None:
                        result["factor"]["rank"] = (rank_row[0] or 0) + 1

                # 첫 매수 거래 (참고용)
                trade = conn.execute(
                    text(
                        "SELECT trade_date, price, quantity, amount FROM trade "
                        "WHERE ticker = :t AND side = 'BUY' "
                        "ORDER BY trade_date ASC LIMIT 1"
                    ),
                    {"t": self._ticker},
                ).fetchone()
                if trade:
                    result["first_buy"] = {
                        "date": str(trade[0]),
                        "price": float(trade[1] or 0),
                        "quantity": int(trade[2] or 0),
                        "amount": float(trade[3] or 0),
                    }

            # 최근 공시 3건
            disc_storage = DartDisclosureStorage()
            with disc_storage.SessionLocal() as session:
                rows = session.execute(
                    text(
                        "SELECT rcept_no, report_nm, pblntf_detail_ty, rcept_dt "
                        "FROM dart_disclosures WHERE stock_code = :t "
                        "ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 3"
                    ),
                    {"t": self._ticker},
                ).fetchall()
                result["disclosures"] = [
                    {
                        "rcept_no": r[0],
                        "report_nm": r[1],
                        "pblntf_detail_ty": r[2],
                        "rcept_dt": r[3],
                    }
                    for r in rows
                ]

            self.finished.emit(result)
        except Exception as e:
            logger.error("종목 상세 로딩 오류: %s", e, exc_info=True)
            self.error.emit(str(e))


class StockDetailDialog(QDialog):
    """종목 상세 팝업"""

    def __init__(
        self,
        ticker: str,
        holding: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Args:
            ticker: 종목코드
            holding: PortfolioView가 가진 잔고 row dict.
                {ticker, name, qty, avg_price, current_price, eval_amount,
                 eval_profit, profit_rate}
        """
        super().__init__(parent)
        self._ticker = ticker
        self._holding = holding
        self._name = holding.get("name") or ticker
        self.setWindowTitle(f"{self._name} ({ticker}) 상세")
        self.setMinimumSize(520, 560)
        self._worker: Optional[_DetailLoadWorker] = None
        self._setup_ui()
        self._start_load()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # 헤더 — 종목명/코드
        header = QLabel(f"<b style='font-size:14pt;'>{self._name}</b>"
                        f" <span style='color:gray;'>({self._ticker})</span>")
        header.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(header)

        # 1) 매수 정보 + 현재 상태 (잔고 dict 기반 — 즉시 표시)
        cur_box = QGroupBox("매수/현재 상태")
        cur_form = QFormLayout(cur_box)
        avg_price = self._holding.get("avg_price", 0)
        qty = self._holding.get("qty", 0)
        cur_price = self._holding.get("current_price", 0)
        eval_amt = self._holding.get("eval_amount", 0)
        eval_profit = self._holding.get("eval_profit", 0)
        profit_rate = self._holding.get("profit_rate", 0)
        buy_amount = avg_price * qty

        cur_form.addRow("수량:", QLabel(f"{qty:,}주"))
        cur_form.addRow("평균 매수가:", QLabel(_fmt_currency(avg_price)))
        cur_form.addRow("매수 금액:", QLabel(_fmt_currency(buy_amount)))
        cur_form.addRow("현재가:", QLabel(_fmt_currency(cur_price)))
        cur_form.addRow("평가 금액:", QLabel(_fmt_currency(eval_amt)))
        rate_lbl = QLabel(
            f"<b style='color:{'#FA5252' if profit_rate >= 0 else '#4DABF7'};'>"
            f"{_fmt_pct(profit_rate)} ({_fmt_currency(eval_profit)})</b>"
        )
        rate_lbl.setTextFormat(Qt.TextFormat.RichText)
        cur_form.addRow("수익률:", rate_lbl)
        root.addWidget(cur_box)

        # 2) 매수일 (DB 조회 후 채움) + 비중 + 팩터 점수
        self._first_buy_box = QGroupBox("매수 이력 / 포트폴리오 비중")
        self._first_buy_form = QFormLayout(self._first_buy_box)
        self._first_buy_form.addRow(QLabel("로딩 중..."))
        root.addWidget(self._first_buy_box)

        self._factor_box = QGroupBox("팩터 점수 (리밸런싱 시점)")
        self._factor_form = QFormLayout(self._factor_box)
        self._factor_form.addRow(QLabel("로딩 중..."))
        root.addWidget(self._factor_box)

        # 3) 최근 공시 3건
        self._disc_box = QGroupBox("최근 공시 (최근 3건)")
        self._disc_layout = QVBoxLayout(self._disc_box)
        self._disc_layout.addWidget(QLabel("로딩 중..."))
        root.addWidget(self._disc_box)

        # 4) 외부 링크 버튼
        link_row = QHBoxLayout()
        dart_btn = QPushButton("DART 페이지")
        dart_btn.clicked.connect(self._open_dart)
        link_row.addWidget(dart_btn)
        naver_btn = QPushButton("네이버 증권")
        naver_btn.clicked.connect(self._open_naver)
        link_row.addWidget(naver_btn)
        link_row.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        link_row.addWidget(close_btn)
        root.addLayout(link_row)

    # ── 로딩 ──

    def _start_load(self) -> None:
        self._worker = _DetailLoadWorker(self._ticker, self)
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, data: dict) -> None:
        self._fill_first_buy(data)
        self._fill_factor(data)
        self._fill_disclosures(data)

    def _on_error(self, msg: str) -> None:
        logger.warning("종목 상세 로딩 실패: %s", msg)
        self._clear_form(self._first_buy_form)
        self._first_buy_form.addRow(QLabel(f"조회 실패: {msg}"))
        self._clear_form(self._factor_form)
        self._factor_form.addRow(QLabel("조회 실패"))
        self._clear_layout(self._disc_layout)
        self._disc_layout.addWidget(QLabel("조회 실패"))

    @staticmethod
    def _clear_form(form: QFormLayout) -> None:
        while form.count():
            item = form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _fill_first_buy(self, data: dict) -> None:
        self._clear_form(self._first_buy_form)
        portfolio = data.get("portfolio")
        first_buy = data.get("first_buy")

        if first_buy:
            self._first_buy_form.addRow("최초 매수일:", QLabel(first_buy["date"]))
            self._first_buy_form.addRow(
                "최초 매수가:", QLabel(_fmt_currency(first_buy["price"]))
            )
            self._first_buy_form.addRow(
                "최초 매수 수량:", QLabel(f"{first_buy['quantity']:,}주")
            )
        else:
            self._first_buy_form.addRow(QLabel("거래 이력 없음"))

        if portfolio:
            self._first_buy_form.addRow(
                "리밸런싱 일자:", QLabel(portfolio["rebalance_date"])
            )
            self._first_buy_form.addRow(
                "목표 비중:", QLabel(f"{portfolio['weight'] * 100:.2f}%")
            )

    def _fill_factor(self, data: dict) -> None:
        self._clear_form(self._factor_form)
        factor = data.get("factor")
        if not factor:
            self._factor_form.addRow(QLabel("팩터 데이터 없음"))
            return

        self._factor_form.addRow(
            "Value 점수:", QLabel(f"{factor['value_score']:.2f}")
        )
        self._factor_form.addRow(
            "Momentum 점수:", QLabel(f"{factor['momentum_score']:.2f}")
        )
        self._factor_form.addRow(
            "Quality 점수:", QLabel(f"{factor['quality_score']:.2f}")
        )
        self._factor_form.addRow(
            "복합 점수:", QLabel(f"{factor['composite_score']:.2f}")
        )
        rank = factor.get("rank")
        if rank is not None:
            self._factor_form.addRow("순위:", QLabel(f"{rank}위"))

    def _fill_disclosures(self, data: dict) -> None:
        self._clear_layout(self._disc_layout)
        disclosures = data.get("disclosures", [])
        if not disclosures:
            self._disc_layout.addWidget(QLabel("최근 공시 없음"))
            return

        for d in disclosures:
            rcept_dt = d.get("rcept_dt", "")
            if len(rcept_dt) == 8:
                date_str = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
            else:
                date_str = rcept_dt
            title = d.get("report_nm", "")
            url = _DART_DOC_FMT.format(rcept_no=d["rcept_no"])
            label = QLabel(
                f"<a href='{url}'>{date_str} — {title}</a>"
            )
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            label.setWordWrap(True)
            self._disc_layout.addWidget(label)

    # ── 외부 링크 ──

    def _open_dart(self) -> None:
        url = _DART_SEARCH_FMT.format(name=quote(self._name))
        webbrowser.open(url)

    def _open_naver(self) -> None:
        url = _NAVER_FMT.format(ticker=self._ticker)
        webbrowser.open(url)
