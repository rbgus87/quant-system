# dart_notifier/notifier.py
"""DART 공시 알림 핵심 로직

보유 종목의 신규 공시를 감지하여 Telegram으로 알림한다.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from config.settings import settings
from dart_notifier.filter import classify_disclosure, get_disclosure_type_name
from dart_notifier.storage import DartDisclosureStorage
from data.dart_client import DART_BASE_URL, DartClient
from data.storage import DataStorage, Portfolio

logger = logging.getLogger(__name__)

# DART 공시목록 API 최대 페이지 크기
_PAGE_COUNT = 100


class DartDisclosureNotifier:
    """보유 종목 대상 DART 공시 알림기

    Args:
        dart_client: DartClient 인스턴스 (없으면 새로 생성)
        db_storage: DataStorage 인스턴스 (보유종목 조회용)
        disc_storage: DartDisclosureStorage (중복 방지용)
    """

    def __init__(
        self,
        dart_client: Optional[DartClient] = None,
        db_storage: Optional[DataStorage] = None,
        disc_storage: Optional[DartDisclosureStorage] = None,
    ) -> None:
        self._dart = dart_client or DartClient()
        self._db = db_storage or DataStorage()
        self._disc_storage = disc_storage or DartDisclosureStorage()
        self._daily_api_count: int = 0
        self._daily_api_count_date: Optional[str] = None

    # ───────────────────────────────────────────────
    # 보유 종목 조회
    # ───────────────────────────────────────────────

    def _get_held_tickers(self) -> dict[str, str]:
        """Portfolio 테이블에서 최신 보유 종목을 조회한다 (캐시 없음).

        Returns:
            {ticker: name} 매핑
        """
        with self._db.SessionLocal() as session:
            # 최신 리밸런싱 날짜 조회
            latest_date = (
                session.query(Portfolio.rebalance_date)
                .order_by(Portfolio.rebalance_date.desc())
                .limit(1)
                .scalar()
            )
            if latest_date is None:
                logger.debug("Portfolio 테이블 비어있음")
                return {}

            rows = (
                session.query(Portfolio.ticker, Portfolio.name)
                .filter(Portfolio.rebalance_date == latest_date)
                .all()
            )
            result = {r.ticker: (r.name or r.ticker) for r in rows}
            logger.debug(
                "보유 종목 조회: %d개 (리밸런싱일: %s)", len(result), latest_date
            )
            return result

    # ───────────────────────────────────────────────
    # DART API 호출
    # ───────────────────────────────────────────────

    def _track_api_call(self) -> None:
        """일일 API 호출 카운트를 추적하고, 8,000건 초과 시 경고한다."""
        today = datetime.now().strftime("%Y%m%d")
        if self._daily_api_count_date != today:
            self._daily_api_count = 0
            self._daily_api_count_date = today
        self._daily_api_count += 1

        if self._daily_api_count == 8000:
            logger.warning(
                "DART API 일일 호출 8,000건 도달 (한도 10,000건). "
                "폴링 간격 확대를 검토하세요."
            )
        if self._daily_api_count % 100 == 0:
            logger.info("DART API 일일 호출: %d건", self._daily_api_count)

    def _fetch_disclosure_list(
        self,
        bgn_de: str,
        end_de: str,
        page_no: int = 1,
    ) -> tuple[list[dict], int]:
        """DART 공시목록 API를 호출한다.

        Args:
            bgn_de: 시작일 (YYYYMMDD)
            end_de: 종료일 (YYYYMMDD)
            page_no: 페이지 번호

        Returns:
            (공시 목록, 전체 건수) 튜플

        Raises:
            Exception: API 호출 실패 시 (재시도 후에도)
        """
        self._track_api_call()
        resp = self._dart._request_with_retry(
            f"{DART_BASE_URL}/list.json",
            params={
                "crtfc_key": self._dart.api_key,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page_no),
                "page_count": str(_PAGE_COUNT),
            },
            max_retries=3,
            timeout=30,
        )
        data = resp.json()

        status = data.get("status", "")
        if status == "013":
            return [], 0
        if status != "000":
            msg = data.get("message", "")
            logger.warning("DART 공시목록 API 오류: status=%s, message=%s", status, msg)
            return [], 0

        total_count = int(data.get("total_count", 0))
        items = data.get("list", [])
        return items, total_count

    def _fetch_all_disclosures(self, bgn_de: str, end_de: str) -> list[dict]:
        """날짜 범위의 전체 공시를 페이징하여 수집한다."""
        all_items: list[dict] = []

        items, total_count = self._fetch_disclosure_list(bgn_de, end_de, page_no=1)
        all_items.extend(items)

        if total_count == 0:
            return all_items

        total_pages = (total_count + _PAGE_COUNT - 1) // _PAGE_COUNT
        for page in range(2, total_pages + 1):
            time.sleep(0.3)
            items, _ = self._fetch_disclosure_list(bgn_de, end_de, page_no=page)
            all_items.extend(items)

        return all_items

    # ───────────────────────────────────────────────
    # 메시지 포맷
    # ───────────────────────────────────────────────

    @staticmethod
    def _format_instant_message(
        stock_name: str,
        stock_code: str,
        disclosure: dict,
    ) -> str:
        """즉시 알림 메시지를 포맷한다."""
        rcept_no = disclosure.get("rcept_no", "")
        report_nm = disclosure.get("report_nm", "")
        pblntf_detail_ty = disclosure.get("pblntf_detail_ty", "")
        rcept_dt = disclosure.get("rcept_dt", "")

        type_name = get_disclosure_type_name(pblntf_detail_ty)
        dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

        # rcept_dt 포맷: YYYYMMDD → YYYY-MM-DD
        if len(rcept_dt) == 8:
            rcept_dt_fmt = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
        else:
            rcept_dt_fmt = rcept_dt

        lines = [
            f"📢 [공시] {stock_name} ({stock_code})",
            f"유형: {type_name}",
            f"제목: {report_nm}",
            f"접수일시: {rcept_dt_fmt}",
            f"링크: {dart_url}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_daily_summary(disclosures: list[dict], ticker_names: dict[str, str]) -> str:
        """일일 요약 메시지를 포맷한다."""
        if not disclosures:
            return ""

        lines = ["📋 [DART 일일 공시 요약]", ""]
        for d in disclosures:
            stock_code = d.get("stock_code", "")
            name = ticker_names.get(stock_code, stock_code)
            report_nm = d.get("report_nm", "")
            rcept_no = d.get("rcept_no", "")
            pblntf_detail_ty = d.get("pblntf_detail_ty", "")
            type_name = get_disclosure_type_name(pblntf_detail_ty)
            dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
            lines.append(f"• {name} | {type_name}")
            lines.append(f"  {report_nm}")
            lines.append(f"  {dart_url}")
            lines.append("")

        lines.append(f"총 {len(disclosures)}건")
        return "\n".join(lines)

    # ───────────────────────────────────────────────
    # 폴링 핵심 로직
    # ───────────────────────────────────────────────

    def poll(self) -> int:
        """신규 공시를 폴링하여 즉시 알림을 발송한다.

        Returns:
            발송한 즉시 알림 수
        """
        if not self._dart.api_key:
            logger.warning("DART_API_KEY 미설정, 공시 알림 건너뜀")
            return 0

        # 1. 보유 종목 fresh read
        held = self._get_held_tickers()
        if not held:
            logger.debug("보유 종목 없음, 공시 폴링 건너뜀")
            return 0

        # corp_code → ticker 역매핑 구축
        corp_to_ticker: dict[str, str] = {}
        for ticker in held:
            corp_code = self._dart.corp_code_map.get(ticker)
            if corp_code:
                corp_to_ticker[corp_code] = ticker

        if not corp_to_ticker:
            logger.warning("보유 종목의 DART corp_code 매핑 실패")
            return 0

        # 2. 오늘 공시 조회 (한 번에 전체)
        today = datetime.now().strftime("%Y%m%d")
        try:
            all_disclosures = self._fetch_all_disclosures(today, today)
        except Exception as e:
            logger.error("DART 공시목록 조회 실패: %s", e)
            return 0

        # 3. 보유 종목 필터링
        relevant = []
        for d in all_disclosures:
            corp_code = d.get("corp_code", "")
            if corp_code in corp_to_ticker:
                d["_ticker"] = corp_to_ticker[corp_code]
                relevant.append(d)

        if not relevant:
            logger.debug("보유 종목 관련 공시 없음 (전체 %d건)", len(all_disclosures))
            return 0

        # 4. 분류 + 중복 방지 + 알림
        sent_count = 0
        for d in relevant:
            rcept_no = d.get("rcept_no", "")
            ticker = d["_ticker"]
            corp_code = d.get("corp_code", "")
            report_nm = d.get("report_nm", "")
            pblntf_detail_ty = d.get("pblntf_detail_ty")
            rcept_dt = d.get("rcept_dt", today)

            category = classify_disclosure(pblntf_detail_ty)

            # 중복 방지: INSERT OR IGNORE
            is_new = self._disc_storage.mark_notified(
                rcept_no=rcept_no,
                corp_code=corp_code,
                stock_code=ticker,
                report_nm=report_nm,
                pblntf_detail_ty=pblntf_detail_ty,
                rcept_dt=rcept_dt,
                category=category,
            )
            if not is_new:
                continue

            # 즉시 알림
            if category == "instant":
                msg = self._format_instant_message(
                    stock_name=held.get(ticker, ticker),
                    stock_code=ticker,
                    disclosure=d,
                )
                self._send_telegram(msg)
                sent_count += 1
                logger.info(
                    "DART 즉시 알림: %s %s (%s)",
                    ticker,
                    report_nm,
                    get_disclosure_type_name(pblntf_detail_ty),
                )

        if relevant:
            logger.info(
                "DART 폴링 완료: 보유종목 관련 %d건 (신규: 즉시=%d, 요약 대기=%d)",
                len(relevant),
                sent_count,
                len(relevant) - sent_count,
            )
        return sent_count

    def send_daily_summary(self) -> int:
        """오늘자 일일 요약 공시를 한 번에 발송한다.

        Returns:
            요약에 포함된 공시 수
        """
        today = datetime.now().strftime("%Y%m%d")
        summaries = self._disc_storage.get_unnotified_daily_summaries(today)

        if not summaries:
            logger.info("DART 일일 요약: 오늘자 공시 없음")
            return 0

        held = self._get_held_tickers()
        msg = self._format_daily_summary(summaries, held)
        if msg:
            self._send_telegram(msg)
            logger.info("DART 일일 요약 발송: %d건", len(summaries))

        return len(summaries)

    def _send_telegram(self, message: str) -> None:
        """텔레그램으로 메시지를 발송한다."""
        try:
            from notify.telegram import TelegramNotifier

            notifier = TelegramNotifier()
            notifier.send(message, parse_mode="")
        except Exception as e:
            logger.error("DART 공시 텔레그램 발송 실패: %s", e)
