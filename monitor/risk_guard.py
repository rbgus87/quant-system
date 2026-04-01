# monitor/risk_guard.py
"""리스크 감시 모듈 (알림 전용 — 자동 매도 없음)

장중 주기적으로 보유 종목의 리스크를 점검하고,
기준 초과 시 Telegram 경고를 발송한다.
"""

import json
import logging
from datetime import datetime
from typing import Optional
from urllib.request import urlopen, Request

from config.settings import settings

logger = logging.getLogger(__name__)


class RiskGuard:
    """리스크 감시 (알림 전용)

    Args:
        cfg: MonitoringConfig.risk_guard (RiskGuardConfig)
    """

    def __init__(self, cfg: Optional[object] = None) -> None:
        if cfg is None:
            cfg = settings.monitoring.risk_guard
        self._cfg = cfg
        self._today_alerts: set[tuple[str, str, str]] = set()  # (date, ticker, type)
        self._today_str: str = ""  # 날짜 변경 시 alerts 초기화
        self._delisting_cache: dict[str, set[str]] = {}  # {date_str: set(codes)}

    def _reset_if_new_day(self) -> str:
        """날짜가 바뀌면 _today_alerts를 초기화하고 오늘 날짜를 반환한다."""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._today_alerts.clear()
        return today

    def check_all(self, balance: dict) -> list[dict]:
        """모든 리스크 체크를 실행하고 경고 목록을 반환한다.

        Args:
            balance: KiwoomRestClient.get_balance() 결과

        Returns:
            경고 dict 리스트
        """
        if not self._cfg.enabled:
            return []

        alerts: list[dict] = []
        alerts.extend(self._check_stop_loss(balance))
        alerts.extend(self._check_portfolio_drawdown(balance))
        alerts.extend(self._check_delisting(balance))
        return alerts

    def _check_stop_loss(self, balance: dict) -> list[dict]:
        """종목별 손절 경고를 확인한다.

        Args:
            balance: get_balance() 결과

        Returns:
            stop_loss 경고 리스트
        """
        today = self._reset_if_new_day()
        alerts: list[dict] = []
        threshold = self._cfg.stop_loss_pct

        for h in balance.get("holdings", []):
            ticker = h.get("ticker", "")
            profit_rate = h.get("profit_rate", 0.0)

            if profit_rate > threshold:
                continue

            alert_key = (today, ticker, "stop_loss")
            if alert_key in self._today_alerts:
                continue

            self._today_alerts.add(alert_key)
            alerts.append({
                "type": "stop_loss",
                "ticker": ticker,
                "name": h.get("name", ticker),
                "current_price": h.get("current_price", 0),
                "avg_price": h.get("avg_price", 0),
                "profit_rate": profit_rate,
                "threshold": threshold,
            })

        return alerts

    def _check_portfolio_drawdown(self, balance: dict) -> list[dict]:
        """포트폴리오 전체 드로다운 경고를 확인한다.

        Args:
            balance: get_balance() 결과

        Returns:
            drawdown 경고 리스트 (최대 1건)
        """
        today = self._reset_if_new_day()
        alert_key = (today, "PORTFOLIO", "drawdown")
        if alert_key in self._today_alerts:
            return []

        total_eval = balance.get("total_eval_amount", 0)
        total_profit = balance.get("total_profit", 0)
        invested = total_eval - total_profit if total_profit else total_eval

        if invested <= 0:
            return []

        loss_pct = total_profit / invested * 100
        threshold = self._cfg.max_drawdown_alert_pct

        if loss_pct > threshold:
            return []

        self._today_alerts.add(alert_key)
        return [{
            "type": "drawdown",
            "ticker": "",
            "name": "",
            "total_eval": total_eval,
            "invested": invested,
            "loss_pct": loss_pct,
            "threshold": threshold,
        }]

    def _check_delisting(self, balance: dict) -> list[dict]:
        """보유 종목 중 관리종목 여부를 확인한다.

        Args:
            balance: get_balance() 결과

        Returns:
            delisting 경고 리스트
        """
        today = self._reset_if_new_day()
        delisted = self._get_delisting_codes(today)
        if delisted is None:
            return []

        alerts: list[dict] = []
        for h in balance.get("holdings", []):
            ticker = h.get("ticker", "")
            if ticker not in delisted:
                continue

            alert_key = (today, ticker, "delisting")
            if alert_key in self._today_alerts:
                continue

            self._today_alerts.add(alert_key)
            alerts.append({
                "type": "delisting",
                "ticker": ticker,
                "name": h.get("name", ticker),
                "qty": h.get("qty", 0),
                "current_price": h.get("current_price", 0),
            })

        return alerts

    def refresh_delisting_cache(self) -> None:
        """관리종목 목록을 조회하여 캐시에 저장한다 (하루 1회)."""
        today = datetime.now().strftime("%Y-%m-%d")
        codes = self._fetch_delisting_codes()
        if codes is not None:
            self._delisting_cache[today] = codes
            logger.info("관리종목 캐시 갱신: %d건 (%s)", len(codes), today)

    def _get_delisting_codes(self, date_str: str) -> Optional[set[str]]:
        """캐시된 관리종목 코드를 반환한다. 캐시 없으면 None."""
        return self._delisting_cache.get(date_str)

    @staticmethod
    def _fetch_delisting_codes() -> Optional[set[str]]:
        """KRX 관리종목 목록을 조회한다.

        Returns:
            관리종목 코드 set, 조회 실패 시 None
        """
        # 1차: KRX Open API
        try:
            krx_key = settings.krx_openapi_key
            if krx_key:
                import requests

                url = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_isu_base_info"
                headers = {"AUTH_KEY": krx_key}
                params = {"basDd": datetime.now().strftime("%Y%m%d")}
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("OutBlock_1", [])
                    codes: set[str] = set()
                    for item in items:
                        if item.get("admisu_yn") == "Y":
                            code = item.get("isu_srt_cd", "").lstrip("A")
                            if code:
                                codes.add(code)
                    if codes:
                        return codes
        except Exception as e:
            logger.warning("KRX Open API 관리종목 조회 실패: %s", e)

        # 2차: KRX 웹 직접 호출 (API key 불필요)
        try:
            result = RiskGuard._fetch_krx_admin_direct()
            if result:
                return result
        except Exception as e:
            logger.warning("KRX 웹 관리종목 조회 실패: %s", e)

        # 3차: FinanceDataReader 폴백
        try:
            import FinanceDataReader as fdr

            listing = fdr.StockListing("KRX-ADMIN")
            if listing is not None and not listing.empty:
                code_col = "Code" if "Code" in listing.columns else listing.columns[0]
                codes = set(listing[code_col].astype(str).str.zfill(6))
                return codes
        except Exception as e:
            logger.warning("FinanceDataReader 관리종목 조회 실패: %s", e)

        logger.warning("관리종목 조회 실패 (모든 소스) — 관리종목 체크 스킵")
        return None

    @staticmethod
    def _fetch_krx_admin_direct() -> Optional[set[str]]:
        """KRX data.krx.co.kr 웹 API로 관리종목 목록을 직접 조회한다.

        Returns:
            관리종목 코드 set, 조회 실패 시 None
        """
        url = "http://data.krx.co.kr/comm/bldAttend/getJsonData.cmd"
        payload = (
            "bld=dbms/MDC/STAT/issue/MDCSTAT23802"
            "&locale=ko_KR"
            "&mktTpCd=0"
            "&isuSrtCd="
            "&csvxls_isNo=false"
        )
        req = Request(
            url,
            data=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))

        items = data.get("OutBlock_1", [])
        if not items:
            return None

        codes: set[str] = set()
        for item in items:
            code = item.get("ISU_SRT_CD", "")
            if code:
                codes.add(code)
        return codes if codes else None
