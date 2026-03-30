# gui/widgets/emergency_panel.py
"""비상 전량 매도 버튼 + .env 관리"""

import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class _EmergencySellWorker(QThread):
    """전량 매도를 백그라운드에서 실행"""

    finished = pyqtSignal(list)  # 매도 결과 리스트
    error = pyqtSignal(str)

    def run(self) -> None:
        try:
            import time
            from datetime import date

            from config.settings import settings
            from data.storage import DataStorage
            from trading.kiwoom_api import KiwoomRestClient

            api = KiwoomRestClient()
            storage = DataStorage()
            balance = api.get_balance()
            holdings = balance.get("holdings", [])
            exchange = "KRX" if api.is_paper else "SOR"
            cost = settings.trading

            results = []
            for h in holdings:
                ticker = h.get("ticker", "")
                qty = h.get("qty", 0)
                name = h.get("name", "")
                if qty > 0:
                    try:
                        result = api.sell_stock(
                            ticker=ticker,
                            qty=qty,
                            price=0,
                            order_type="3",
                            exchange=exchange,
                        )
                        if result.get("return_code") == 0:
                            ord_no = result.get("ord_no", "")
                            results.append(
                                f"{name}({ticker}) {qty}주 매도 주문 (#{ord_no})"
                            )
                            price = h.get("current_price", 0)
                            amount = price * qty
                            storage.save_trade(
                                trade_date=date.today(),
                                ticker=ticker,
                                side="SELL",
                                quantity=qty,
                                price=price,
                                amount=amount,
                                commission=amount * cost.commission_rate,
                                tax=amount * cost.tax_rate,
                                is_paper=settings.is_paper_trading,
                            )
                        else:
                            msg = result.get("return_msg", "알 수 없는 오류")
                            results.append(f"{name}({ticker}) 매도 실패: {msg}")
                    except Exception as e:
                        results.append(f"{name}({ticker}) 매도 실패: {e}")
                    time.sleep(1.0)

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class EmergencyPanel(QWidget):
    """비상 매도 + .env 편집"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_EmergencySellWorker] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 비상 매도
        emergency_group = QGroupBox("비상 조치")
        emergency_layout = QVBoxLayout(emergency_group)

        self._sell_btn = QPushButton("전량 매도")
        self._sell_btn.setStyleSheet(
            "QPushButton { background-color: #FF4444; color: white; "
            "font-weight: bold; padding: 8px; font-size: 13px; }"
            "QPushButton:hover { background-color: #CC0000; }"
        )
        self._sell_btn.clicked.connect(self._confirm_sell_all)
        emergency_layout.addWidget(self._sell_btn)

        self._sell_status = QLabel("")
        self._sell_status.setWordWrap(True)
        emergency_layout.addWidget(self._sell_status)

        layout.addWidget(emergency_group)

        # .env 관리
        env_group = QGroupBox("환경변수 (.env)")
        env_layout = QFormLayout(env_group)

        self._env_fields: dict[str, QLineEdit] = {}
        env_keys = [
            ("KIWOOM_APP_KEY", "키움 앱 키"),
            ("KIWOOM_APP_SECRET", "키움 앱 시크릿"),
            ("KIWOOM_ACCOUNT_NO", "계좌번호"),
            ("TELEGRAM_BOT_TOKEN", "텔레그램 봇 토큰"),
            ("TELEGRAM_CHAT_ID", "텔레그램 채팅 ID"),
            ("IS_PAPER_TRADING", "모의투자 (true/false)"),
        ]

        for key, label in env_keys:
            field = QLineEdit()
            if "SECRET" in key or "TOKEN" in key:
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self._env_fields[key] = field
            env_layout.addRow(f"{label}:", field)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("불러오기")
        load_btn.clicked.connect(self._load_env)
        btn_row.addWidget(load_btn)

        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._save_env)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()

        self._env_status = QLabel("")
        btn_row.addWidget(self._env_status)

        env_layout.addRow(btn_row)
        layout.addWidget(env_group)

        layout.addStretch()

        # 초기 로드
        self._load_env()

    def _confirm_sell_all(self) -> None:
        """전량 매도 확인 — '매도' 텍스트 입력 필요"""
        text, ok = QInputDialog.getText(
            self,
            "전량 매도 확인",
            '정말로 모든 보유 종목을 시장가로 매도하려면\n"매도"를 입력하세요:',
        )
        if ok and text.strip() == "매도":
            self._execute_sell_all()
        elif ok:
            QMessageBox.warning(self, "취소됨", '"매도"를 정확히 입력해야 합니다.')

    def _execute_sell_all(self) -> None:
        """전량 매도 실행"""
        self._sell_btn.setEnabled(False)
        self._sell_btn.setText("매도 중...")
        self._sell_status.setText("전량 매도 주문 실행 중...")

        self._worker = _EmergencySellWorker()
        self._worker.finished.connect(self._on_sell_finished)
        self._worker.error.connect(self._on_sell_error)
        self._worker.start()

    def _on_sell_finished(self, results: list) -> None:
        self._sell_btn.setEnabled(True)
        self._sell_btn.setText("전량 매도")
        if results:
            self._sell_status.setText("\n".join(results))
        else:
            self._sell_status.setText("보유 종목 없음")

    def _on_sell_error(self, error_msg: str) -> None:
        self._sell_btn.setEnabled(True)
        self._sell_btn.setText("전량 매도")
        self._sell_status.setText(f"매도 실패: {error_msg}")
        self._sell_status.setStyleSheet("color: red;")

    def _load_env(self) -> None:
        """현재 .env 파일 읽기"""
        env_path = Path(".env")
        if not env_path.exists():
            self._env_status.setText(".env 파일 없음")
            return

        try:
            values = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()

            for key, field in self._env_fields.items():
                field.setText(values.get(key, ""))

            self._env_status.setText("불러옴")
            self._env_status.setStyleSheet("color: green;")
        except Exception as e:
            self._env_status.setText(f"로드 실패: {e}")
            self._env_status.setStyleSheet("color: red;")

    def _save_env(self) -> None:
        """수정된 값을 .env 파일에 저장"""
        env_path = Path(".env")

        try:
            # 기존 파일 읽기 (순서/주석 보존)
            existing_lines = []
            existing_keys = set()
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        key = stripped.split("=", 1)[0].strip()
                        if key in self._env_fields:
                            new_val = self._env_fields[key].text()
                            existing_lines.append(f"{key}={new_val}")
                            existing_keys.add(key)
                            continue
                    existing_lines.append(line)

            # 새로운 키 추가
            for key, field in self._env_fields.items():
                if key not in existing_keys and field.text():
                    existing_lines.append(f"{key}={field.text()}")

            env_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
            self._show_toast(self._env_status, "저장 완료", "green")
        except Exception as e:
            self._show_toast(self._env_status, f"저장 실패: {e}", "red", 5000)

    def _show_toast(self, label: QLabel, text: str, color: str, duration_ms: int = 3000) -> None:
        """일시적 상태 메시지 표시 (자동 사라짐)"""
        label.setText(text)
        label.setStyleSheet(f"color: {color};")
        QTimer.singleShot(duration_ms, lambda: label.setText(""))
