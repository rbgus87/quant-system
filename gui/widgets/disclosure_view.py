"""DART 공시 뷰 — 보유 종목/전체 공시 이력 테이블

데이터 소스: monitor.db의 dart_disclosures 테이블 (DartDisclosureStorage가 적재)
보유 종목: quant.db의 Portfolio 테이블 (최신 rebalance_date 기준)
"""

import logging
import webbrowser
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.themes import accent_palette

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL_MS = 300_000  # 5분

# DART URL 패턴 (notifier.py와 동일)
_DART_URL_FMT = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 공시 유형 코드 → 한글명 (filter.py 매핑 재사용)
_TYPE_NAMES: dict[str, str] = {
    "A001": "사업보고서", "A002": "반기보고서", "A003": "분기보고서",
    "B001": "주요사항보고서", "B002": "주요경영사항신고", "B003": "최대주주변경",
    "E001": "불성실공시법인지정", "E002": "공정공시", "E003": "시장조치·안내",
    "G001": "전환사채발행", "G002": "신주인수권부사채발행",
    "G003": "유상증자", "G004": "무상증자",
    "H001": "합병", "H002": "분할", "H003": "분할합병",
    "I001": "주식교환·이전", "I002": "자기주식취득·처분",
}

# 중요도 분류 (사용자 명세 기준)
# 🔴 긴급: 유상증자/감자/상장폐지/감사의견거절/합병/분할 → 코드 + 제목 키워드
_URGENT_CODES: set[str] = {"G003", "H001", "H002", "H003", "B001", "E001"}
_URGENT_KEYWORDS: tuple[str, ...] = (
    "감자", "상장폐지", "감사의견거절", "감사의견 거절",
    "관리종목", "회생절차", "거래정지",
)
# 🟡 주의: 실적/최대주주변경/전환사채/공정공시
_WARN_CODES: set[str] = {
    "A001", "A002", "A003", "B003", "G001", "G002", "E002", "I002",
}


def classify_priority(pblntf_detail_ty: Optional[str], report_nm: str) -> str:
    """공시 중요도를 판정한다.

    Returns:
        "urgent" | "warn" | "normal"
    """
    code = (pblntf_detail_ty or "").upper()
    title = report_nm or ""

    # 제목 키워드 우선 (B001 안의 감자/상장폐지 등 잡기)
    if any(kw in title for kw in _URGENT_KEYWORDS):
        return "urgent"
    if code in _URGENT_CODES:
        return "urgent"
    if code in _WARN_CODES:
        return "warn"
    return "normal"


def _priority_display(p: str, is_dark: bool = True) -> tuple[str, QColor]:
    """중요도 → (라벨, 색상). 다크/라이트 모드별 톤 분기."""
    palette = accent_palette(is_dark)
    if p == "urgent":
        return ("🔴 긴급", QColor(palette["urgent"]))
    if p == "warn":
        return ("🟡 주의", QColor(palette["warn"]))
    return ("⚪ 일반", QColor(palette["normal"]))


def _disclosure_type_name(code: Optional[str]) -> str:
    if not code:
        return "기타"
    return _TYPE_NAMES.get(code.upper(), "기타")


class _DisclosureLoadWorker(QThread):
    """공시 + 보유 종목을 백그라운드 로딩"""

    finished = pyqtSignal(list, dict)  # disclosures, ticker_name_map
    error = pyqtSignal(str)

    def __init__(self, days: int, holdings_only: bool, parent=None) -> None:
        super().__init__(parent)
        self._days = days
        self._holdings_only = holdings_only

    def run(self) -> None:
        try:
            from sqlalchemy import text

            from data.storage import DataStorage
            from dart_notifier.storage import DartDisclosureStorage

            # 1) 보유 종목 (최신 rebalance_date)
            held: dict[str, str] = {}
            try:
                storage = DataStorage()
                with storage.engine.connect() as conn:
                    latest = conn.execute(
                        text(
                            "SELECT MAX(rebalance_date) FROM portfolio"
                        )
                    ).scalar()
                    if latest is not None:
                        rows = conn.execute(
                            text(
                                "SELECT ticker, name FROM portfolio "
                                "WHERE rebalance_date = :dt"
                            ),
                            {"dt": str(latest)},
                        ).fetchall()
                        held = {r[0]: (r[1] or r[0]) for r in rows}
            except Exception as e:
                logger.warning("보유 종목 조회 실패: %s", e)

            # 2) 공시 이력
            disc_storage = DartDisclosureStorage()
            cutoff = (datetime.now() - timedelta(days=self._days)).strftime("%Y%m%d")

            with disc_storage.SessionLocal() as session:
                if self._holdings_only and held:
                    placeholders = ",".join(f":t{i}" for i in range(len(held)))
                    params = {f"t{i}": t for i, t in enumerate(held)}
                    params["cutoff"] = cutoff
                    sql = (
                        f"SELECT rcept_no, corp_code, stock_code, report_nm, "
                        f"       pblntf_detail_ty, rcept_dt "
                        f"FROM dart_disclosures "
                        f"WHERE rcept_dt >= :cutoff AND stock_code IN ({placeholders}) "
                        f"ORDER BY rcept_dt DESC, rcept_no DESC"
                    )
                    rows = session.execute(text(sql), params).fetchall()
                elif self._holdings_only and not held:
                    rows = []
                else:
                    rows = session.execute(
                        text(
                            "SELECT rcept_no, corp_code, stock_code, report_nm, "
                            "       pblntf_detail_ty, rcept_dt "
                            "FROM dart_disclosures "
                            "WHERE rcept_dt >= :cutoff "
                            "ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 500"
                        ),
                        {"cutoff": cutoff},
                    ).fetchall()

            disclosures = [
                {
                    "rcept_no": r[0],
                    "corp_code": r[1],
                    "stock_code": r[2],
                    "report_nm": r[3],
                    "pblntf_detail_ty": r[4],
                    "rcept_dt": r[5],
                }
                for r in rows
            ]

            self.finished.emit(disclosures, held)
        except Exception as e:
            logger.error("공시 로딩 오류: %s", e, exc_info=True)
            self.error.emit(str(e))


class DisclosureView(QWidget):
    """DART 공시 이력 + 필터 + 자동 갱신"""

    HEADERS = ["날짜", "종목코드", "종목명", "공시 제목", "중요도"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_DisclosureLoadWorker] = None
        # 명목값. setData(UserRole)로 정렬 시 사용
        self._all_disclosures: list[dict] = []
        self._held_names: dict[str, str] = {}
        self._is_dark = True
        self._setup_ui()
        self._setup_auto_refresh()
        self._apply_palette()

    def set_dark_mode(self, is_dark: bool) -> None:
        """테마 변경 시 색상 재적용 (MainWindow._apply_theme에서 호출)"""
        self._is_dark = is_dark
        self._apply_palette()
        # 행 배경/전경 다시 계산하기 위해 테이블 재렌더
        self._render_table()

    def _apply_palette(self) -> None:
        """색상 의존 위젯(라벨)에 다크/라이트 톤 반영"""
        palette = accent_palette(self._is_dark)
        # "마지막 갱신" 회색 라벨
        if hasattr(self, "_last_label"):
            self._last_label.setStyleSheet(f"color: {palette['muted']};")

    # ── UI ──

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        group = QGroupBox("DART 공시")
        gl = QVBoxLayout(group)

        # 필터 행
        filter_row = QHBoxLayout()

        self._holdings_only_rb = QRadioButton("보유 종목만")
        self._holdings_only_rb.setChecked(True)
        self._all_rb = QRadioButton("전체")
        scope_grp = QButtonGroup(self)
        scope_grp.addButton(self._holdings_only_rb)
        scope_grp.addButton(self._all_rb)
        filter_row.addWidget(self._holdings_only_rb)
        filter_row.addWidget(self._all_rb)

        filter_row.addSpacing(16)
        filter_row.addWidget(QLabel("기간:"))
        self._period_combo = QComboBox()
        self._period_combo.addItem("오늘", 1)
        self._period_combo.addItem("1주", 7)
        self._period_combo.addItem("1개월", 30)
        self._period_combo.setMaximumWidth(100)
        filter_row.addWidget(self._period_combo)

        filter_row.addSpacing(8)
        filter_row.addWidget(QLabel("중요도:"))
        self._priority_combo = QComboBox()
        self._priority_combo.addItem("전체", "all")
        self._priority_combo.addItem("중요만", "urgent_warn")
        self._priority_combo.setMaximumWidth(100)
        filter_row.addWidget(self._priority_combo)

        filter_row.addStretch()

        self._refresh_btn = QPushButton("새로고침")
        self._refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(self._refresh_btn)

        gl.addLayout(filter_row)

        # 필터 변경 시 즉시 재조회 (서버 호출 — 보유종목/기간) 또는 재필터링 (클라이언트 — 중요도)
        self._holdings_only_rb.toggled.connect(self._on_scope_changed)
        self._period_combo.currentIndexChanged.connect(self._on_scope_changed)
        self._priority_combo.currentIndexChanged.connect(self._render_table)

        # 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSortIndicatorShown(True)
        self._table.setSortingEnabled(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setCursor(Qt.CursorShape.PointingHandCursor)
        self._table.cellClicked.connect(self._on_row_clicked)
        gl.addWidget(self._table)

        # 하단 자동 갱신
        bottom = QHBoxLayout()
        self._auto_cb = QCheckBox("자동 갱신 (5분)")
        self._auto_cb.stateChanged.connect(self._toggle_auto)
        bottom.addWidget(self._auto_cb)
        bottom.addStretch()
        self._last_label = QLabel("마지막 갱신: -")
        # 색상은 _apply_palette에서 모드별로 설정
        bottom.addWidget(self._last_label)
        gl.addLayout(bottom)

        layout.addWidget(group)

    def _setup_auto_refresh(self) -> None:
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self.refresh)

    # ── 동작 ──

    def _toggle_auto(self, state: int) -> None:
        if state:
            self._auto_timer.start(_REFRESH_INTERVAL_MS)
            self.refresh()
        else:
            self._auto_timer.stop()

    def _on_scope_changed(self, *_args) -> None:
        self.refresh()

    def refresh(self) -> None:
        """공시 + 보유 종목 백그라운드 재조회"""
        if self._worker and self._worker.isRunning():
            return
        days = self._period_combo.currentData() or 1
        holdings_only = self._holdings_only_rb.isChecked()
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("조회 중...")
        self._worker = _DisclosureLoadWorker(days=days, holdings_only=holdings_only)
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, disclosures: list, held: dict) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._all_disclosures = disclosures
        self._held_names = held
        self._render_table()
        self._last_label.setText(
            f"마지막 갱신: {datetime.now().strftime('%H:%M:%S')} "
            f"({len(disclosures)}건)"
        )
        self._apply_palette()

    def _on_error(self, msg: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("새로고침")
        self._last_label.setText(f"조회 실패: {msg}")
        logger.warning("공시 조회 실패: %s", msg)

    def _render_table(self, *_args) -> None:
        """현재 데이터 + 중요도 필터로 테이블 다시 그리기"""
        priority_filter = self._priority_combo.currentData() or "all"
        rows: list[tuple[dict, str]] = []
        for d in self._all_disclosures:
            p = classify_priority(d.get("pblntf_detail_ty"), d.get("report_nm", ""))
            if priority_filter == "urgent_warn" and p == "normal":
                continue
            rows.append((d, p))

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))

        palette = accent_palette(self._is_dark)
        urgent_bg = QColor(*palette["urgent_bg_rgba"])

        for row_idx, (d, prio) in enumerate(rows):
            rcept_dt = d.get("rcept_dt", "")
            if len(rcept_dt) == 8:
                date_str = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
            else:
                date_str = rcept_dt
            stock_code = d.get("stock_code", "")
            stock_name = self._held_names.get(stock_code, "")
            type_name = _disclosure_type_name(d.get("pblntf_detail_ty"))
            title = d.get("report_nm", "")
            prio_label, prio_color = _priority_display(prio, self._is_dark)

            cells = [
                date_str,
                stock_code,
                stock_name,
                f"[{type_name}] {title}" if type_name != "기타" else title,
                prio_label,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, d.get("rcept_no", ""))
                if col == 4:
                    item.setForeground(prio_color)
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                # 긴급 행 전체에 옅은 배경 (다크/라이트 별 톤)
                if prio == "urgent":
                    item.setBackground(urgent_bg)
                self._table.setItem(row_idx, col, item)

        self._table.setSortingEnabled(True)

    def _on_row_clicked(self, row: int, _col: int) -> None:
        """행 클릭 → DART 원문 URL 브라우저 열기"""
        item = self._table.item(row, 0)
        if item is None:
            return
        rcept_no = item.data(Qt.ItemDataRole.UserRole)
        if not rcept_no:
            return
        url = _DART_URL_FMT.format(rcept_no=rcept_no)
        webbrowser.open(url)
