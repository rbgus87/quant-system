# GUI 전면 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PyQt6 기반 퀀트 시스템 GUI의 전체적 UX/기능 개선 — 포트폴리오 테이블, 차트, 백테스트, 로그 뷰어, 안전장치, 에러 처리 등 10개 영역

**Architecture:** 기존 위젯 구조(main_window.py + widgets/*.py + themes.py) 유지하면서 각 위젯을 개별 개선. 새 파일 생성 최소화, 기존 파일 수정 중심.

**Tech Stack:** Python 3.14, PyQt6, matplotlib, pandas, SQLAlchemy

---

## File Structure

| 파일 | 변경 유형 | 책임 |
|------|----------|------|
| `gui/widgets/portfolio_view.py` | Modify | 정렬, 컬럼 색상 강화, 교대 행 색상 |
| `gui/widgets/chart_view.py` | Modify | 누적 수익률 차트 추가, 차트 타입 전환 |
| `gui/widgets/backtest_runner.py` | Modify | 날짜 피커, 리포트 열기 버튼, 프로그레스바 수정 |
| `gui/widgets/log_viewer.py` | Modify | 검색/필터, 라인 카운트 표시 |
| `gui/widgets/scheduler_panel.py` | Modify | 툴팁 보강, 원샷 실행 중 상태 표시 |
| `gui/widgets/emergency_panel.py` | Modify | 2단계 확인(텍스트 입력), 토스트 피드백 |
| `gui/widgets/status_bar.py` | Modify | 장 운영시간 표시, 모의/실전 표시 |
| `gui/main_window.py` | Modify | 키보드 단축키, 에러 팝업 연결 |
| `gui/themes.py` | Modify | 교대 행 색상, 검색 하이라이트 색상 추가 |

---

## Task 1: 포트폴리오 테이블 개선

**Files:**
- Modify: `gui/widgets/portfolio_view.py`
- Modify: `gui/themes.py`

- [ ] **Step 1: 컬럼 정렬 기능 추가**

`QTableWidget`에 `setSortingEnabled(True)` 설정하고, 숫자 컬럼은 커스텀 정렬을 위해 `QTableWidgetItem`에 `setData(Qt.ItemDataRole.UserRole, numeric_value)` 저장.

```python
# portfolio_view.py: _setup_ui() 테이블 설정 부분
self._table.setSortingEnabled(True)
self._table.horizontalHeader().setSortIndicatorShown(True)
```

```python
# _on_balance_loaded()에서 아이템 생성 시
if col >= 2:  # 숫자 컬럼
    item.setData(Qt.ItemDataRole.UserRole, float_value)
```

`_NumericTableItem(QTableWidgetItem)` 서브클래스 생성하여 `__lt__` 오버라이드로 숫자 기준 정렬.

- [ ] **Step 2: 수익률 색상 강화 + 교대 행 색상**

수익률 색상을 더 선명하게 변경하고, 배경색도 추가:
- 양수: 텍스트 `#FF4444` + 배경 `rgba(255,68,68,0.1)`
- 음수: 텍스트 `#4DABF7` + 배경 `rgba(77,171,247,0.1)`

교대 행 색상 활성화:
```python
self._table.setAlternatingRowColors(True)
```

themes.py에 교대 행 색상 추가:
```css
QTableWidget { alternate-background-color: {alt_bg}; }
```

- [ ] **Step 3: 총 평가 요약 개선**

총 수익률과 총 평가손익 추가 표시:
```python
total_profit = sum(h.get("eval_profit", 0) for h in holdings)
total_rate = (total_profit / total_buy * 100) if total_buy else 0
self._total_label.setText(
    f"총 평가: {total:,.0f}원 | 손익: {total_profit:+,.0f}원 ({total_rate:+.2f}%) | "
    f"예수금: {cash:,.0f}원 | {len(holdings)}종목"
)
```

- [ ] **Step 4: 테스트 실행 및 커밋**

---

## Task 2: 차트 개선 — 누적 수익률 + 차트 타입 전환

**Files:**
- Modify: `gui/widgets/chart_view.py`

- [ ] **Step 1: 차트 타입 콤보박스 추가**

기존 "거래 내역" 차트 외에 "누적 수익률" 차트 타입 추가:
```python
self._chart_type_combo = QComboBox()
self._chart_type_combo.addItems(["거래 내역", "누적 수익률"])
ctrl_row.insertWidget(0, self._chart_type_combo)
```

- [ ] **Step 2: 누적 수익률 차트 구현**

Portfolio 테이블의 일별 총 평가액을 기반으로 누적 수익률 라인 차트:
```python
def _draw_cumulative_return(self, ax, start_date, end_date, tc):
    """일별 포트폴리오 가치 기반 누적 수익률"""
    from data.storage import DataStorage
    ds = DataStorage()
    trades = ds.load_trades(start_date=start_date, end_date=end_date)
    if trades.empty:
        ax.text(0.5, 0.5, "거래 데이터 없음", ...)
        return
    # 일별 실현 손익 누적
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])
    daily_pnl = trades.groupby("trade_date")["profit"].sum().cumsum()
    ax.plot(daily_pnl.index, daily_pnl.values, color="#4DABF7", linewidth=1.5)
    ax.fill_between(daily_pnl.index, daily_pnl.values, alpha=0.15, color="#4DABF7")
    ax.axhline(y=0, color=tc["grid"], linewidth=0.5)
    ax.set_ylabel("누적 손익 (원)", color=tc["fg"])
```

- [ ] **Step 3: refresh()에서 차트 타입 분기**

```python
def refresh(self):
    chart_type = self._chart_type_combo.currentText()
    if chart_type == "누적 수익률":
        self._draw_cumulative_return(ax, start_date, end_date, tc)
    else:
        self._draw_trade_bars(ax, start_date, end_date, tc)
```

기존 바 차트 코드를 `_draw_trade_bars()` 메서드로 추출.

- [ ] **Step 4: 테스트 및 커밋**

---

## Task 3: 백테스트 탭 개선

**Files:**
- Modify: `gui/widgets/backtest_runner.py`

- [ ] **Step 1: QDateEdit 위젯으로 교체**

QLineEdit를 QDateEdit로 교체하여 날짜 선택 오류 방지:
```python
from PyQt6.QtWidgets import QDateEdit
from PyQt6.QtCore import QDate

self._start_edit = QDateEdit()
self._start_edit.setCalendarPopup(True)
self._start_edit.setDate(QDate(2020, 1, 1))
self._start_edit.setDisplayFormat("yyyy-MM-dd")

self._end_edit = QDateEdit()
self._end_edit.setCalendarPopup(True)
self._end_edit.setDate(QDate.currentDate())
self._end_edit.setDisplayFormat("yyyy-MM-dd")
```

- [ ] **Step 2: 리포트 열기 버튼 추가**

백테스트 완료 시 HTML 리포트 파일 경로를 파싱하여 "리포트 열기" 버튼 활성화:
```python
self._open_report_btn = QPushButton("리포트 열기")
self._open_report_btn.setEnabled(False)
self._open_report_btn.clicked.connect(self._open_report)

def _open_report(self):
    if self._report_path and os.path.exists(self._report_path):
        import webbrowser
        webbrowser.open(f"file:///{self._report_path}")
```

`_on_finished()`에서 출력 라인을 스캔하여 리포트 경로 추출:
```python
for line in self._output_lines:
    if "reports/" in line and ".html" in line:
        # 경로 추출 로직
```

- [ ] **Step 3: 프로그레스바를 확정적(determinate) 모드로 변경**

출력에서 진행률 파싱 (예: `[2020] ... [2021] ...` 연도별 진행):
```python
self._progress.setRange(0, 100)
# _read_output()에서 연도 진행 파싱
```

파싱이 불가능한 경우 indeterminate 유지하되, 실행 중 표시를 명확히.

- [ ] **Step 4: 초기자본 입력에 천 단위 콤마 포맷 적용**

```python
self._cash_edit.textChanged.connect(self._format_cash)

def _format_cash(self):
    text = self._cash_edit.text().replace(",", "")
    if text.isdigit():
        self._cash_edit.blockSignals(True)
        self._cash_edit.setText(f"{int(text):,}")
        self._cash_edit.blockSignals(False)
```

- [ ] **Step 5: 테스트 및 커밋**

---

## Task 4: 로그 뷰어 개선

**Files:**
- Modify: `gui/widgets/log_viewer.py`

- [ ] **Step 1: 검색 기능 추가**

검색 입력창 + 이전/다음 버튼:
```python
self._search_input = QLineEdit()
self._search_input.setPlaceholderText("로그 검색...")
self._search_input.returnPressed.connect(self._search_next)

self._search_prev_btn = QPushButton("<")
self._search_prev_btn.setFixedWidth(30)
self._search_next_btn = QPushButton(">")
self._search_next_btn.setFixedWidth(30)
```

검색 실행:
```python
def _search_next(self):
    text = self._search_input.text()
    if not text:
        return
    found = self._text.find(text)
    if not found:
        # 처음부터 재검색
        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._text.setTextCursor(cursor)
        self._text.find(text)
```

- [ ] **Step 2: 레벨 필터 콤보박스 추가**

```python
self._level_filter = QComboBox()
self._level_filter.addItems(["전체", "ERROR", "WARNING", "INFO", "DEBUG"])
```

필터링은 표시/숨김이 아닌 시각적 강조로 구현 (성능 고려).

- [ ] **Step 3: 라인 카운트 표시**

```python
self._line_count_label = QLabel("0 / 2000")
# append_log()에서 업데이트
self._line_count_label.setText(f"{self._line_count} / {self.MAX_LINES}")
```

- [ ] **Step 4: 테스트 및 커밋**

---

## Task 5: UX 개선 — 툴팁, 키보드 단축키

**Files:**
- Modify: `gui/main_window.py`
- Modify: `gui/widgets/scheduler_panel.py`
- Modify: `gui/widgets/preset_panel.py`

- [ ] **Step 1: 전체 버튼 툴팁 추가**

scheduler_panel.py — 이미 일부 있음. 나머지 추가:
```python
self._start_btn.setToolTip("스케줄러 상주 프로세스 시작 (Ctrl+R)")
self._stop_btn.setToolTip("스케줄러 프로세스 중지 (Ctrl+S)")
```

preset_panel.py:
```python
self._apply_btn.setToolTip("변경된 전략 설정을 config.yaml에 저장")
```

- [ ] **Step 2: 키보드 단축키 등록**

main_window.py에 QShortcut 추가:
```python
from PyQt6.QtGui import QKeySequence, QShortcut

QShortcut(QKeySequence("Ctrl+R"), self, self._scheduler_panel.start_scheduler)
QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_theme)
QShortcut(QKeySequence("Ctrl+L"), self, self._log_viewer.clear)
QShortcut(QKeySequence("Ctrl+F"), self, self._focus_log_search)
QShortcut(QKeySequence("F5"), self, self._portfolio_view.refresh)
```

- [ ] **Step 3: 테스트 및 커밋**

---

## Task 6: 긴급매도 안전장치 강화

**Files:**
- Modify: `gui/widgets/emergency_panel.py`

- [ ] **Step 1: 2단계 확인 — 텍스트 입력 확인**

기존 QMessageBox.warning 대신, "매도" 텍스트 입력 요구:
```python
from PyQt6.QtWidgets import QInputDialog

def _confirm_sell_all(self):
    text, ok = QInputDialog.getText(
        self,
        "전량 매도 확인",
        '정말로 모든 보유 종목을 매도하려면\n"매도"를 입력하세요:',
    )
    if ok and text.strip() == "매도":
        self._execute_sell_all()
    elif ok:
        QMessageBox.warning(self, "취소됨", '"매도"를 정확히 입력해야 합니다.')
```

- [ ] **Step 2: .env 저장 후 토스트 피드백 (타이머 기반 자동 사라짐)**

```python
def _show_toast(self, label: QLabel, text: str, color: str, duration_ms: int = 3000):
    label.setText(text)
    label.setStyleSheet(f"color: {color};")
    QTimer.singleShot(duration_ms, lambda: label.setText(""))
```

- [ ] **Step 3: 테스트 및 커밋**

---

## Task 7: 상태바 개선

**Files:**
- Modify: `gui/widgets/status_bar.py`

- [ ] **Step 1: 장 운영시간 + 모의/실전 표시 추가**

```python
self._market_label = QLabel("")
layout.insertWidget(1, self._market_label)

self._mode_label = QLabel("")
layout.addWidget(self._mode_label)

def _update_time(self):
    now = datetime.now()
    self._time_label.setText(now.strftime("%Y-%m-%d %H:%M:%S"))

    # 장 운영시간 표시 (09:00~15:30 평일)
    if now.weekday() < 5:
        market_open = now.replace(hour=9, minute=0, second=0)
        market_close = now.replace(hour=15, minute=30, second=0)
        if market_open <= now <= market_close:
            self._market_label.setText("장 운영 중")
            self._market_label.setStyleSheet("color: #40C057; font-weight: bold;")
        else:
            self._market_label.setText("장 마감")
            self._market_label.setStyleSheet("color: gray;")
    else:
        self._market_label.setText("휴장")
        self._market_label.setStyleSheet("color: gray;")

    # 모의/실전 표시
    import os
    is_paper = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
    if is_paper:
        self._mode_label.setText("[모의투자]")
        self._mode_label.setStyleSheet("color: #4DABF7; font-weight: bold;")
    else:
        self._mode_label.setText("[실전투자]")
        self._mode_label.setStyleSheet("color: #FF6B6B; font-weight: bold;")
```

- [ ] **Step 2: 테스트 및 커밋**

---

## Task 8: 에러 팝업 + 원샷 실행 상태 표시

**Files:**
- Modify: `gui/main_window.py`
- Modify: `gui/widgets/scheduler_panel.py`

- [ ] **Step 1: 스케줄러 패널 — 원샷 프로세스 상태 표시**

원샷 실행(즉시 실행/연결 테스트/스크리닝) 중에 해당 버튼 비활성화 + 텍스트 변경:
```python
def _run_now(self):
    proc = self._create_process()
    proc.setProperty("mode", "oneshot")
    self._now_btn.setEnabled(False)
    self._now_btn.setText("실행 중...")
    proc.finished.connect(lambda: self._reset_oneshot_btn(self._now_btn, "즉시 실행"))
    proc.start(...)
```

- [ ] **Step 2: 에러 발생 시 팝업 알림**

로그 출력에서 ERROR 레벨 감지 시 사용자에게 팝업:
```python
# main_window.py
def _on_error_log(self, line: str):
    """심각한 에러 발생 시 팝업"""
    if "[ERROR]" in line and ("API" in line or "주문" in line or "매매" in line):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "오류 발생", line)
```

주의: 팝업 남발 방지를 위해 매매/API 관련 에러만 팝업.

- [ ] **Step 3: 테스트 및 커밋**

---

## 실행 순서

1. Task 1 (포트폴리오) → 가장 자주 보는 화면
2. Task 2 (차트) → 데이터 시각화 핵심
3. Task 3 (백테스트) → 사용성 개선
4. Task 4 (로그 뷰어) → 디버깅 효율
5. Task 5 (UX) → 전체 사용성
6. Task 6 (긴급매도) → 안전
7. Task 7 (상태바) → 정보 표시
8. Task 8 (에러 팝업) → 알림
