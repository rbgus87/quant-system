# notify/telegram.py
"""텔레그램 알림 모듈

python-telegram-bot v21은 완전 async 기반이지만
스케줄러 내에서 간단히 쓰려면 requests 직접 호출이 더 단순.
"""

import time
import requests
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096


class TelegramNotifier:
    """텔레그램 봇 메시지 발송"""

    def __init__(self) -> None:
        self.token: str = settings.telegram_bot_token
        self.chat_id: str = settings.telegram_chat_id

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """메시지 발송 (4096자 초과 시 분할)

        Args:
            message: 발송할 텍스트
            parse_mode: Markdown 또는 HTML

        Returns:
            True=성공, False=실패
        """
        if not self.token or not self.chat_id:
            logger.warning("텔레그램 설정 없음 (.env 확인)")
            return False

        if len(message) > MAX_MESSAGE_LENGTH:
            return self._send_chunked(message, parse_mode)

        return self._send_single(message, parse_mode)

    def _send_single(
        self,
        message: str,
        parse_mode: str,
        max_retries: int = 3,
    ) -> bool:
        """단일 메시지 발송 (재시도 포함)

        Args:
            message: 발송할 텍스트
            parse_mode: 파싱 모드
            max_retries: 최대 재시도 횟수

        Returns:
            True=성공, False=실패
        """
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    logger.debug("텔레그램 발송 성공")
                    return True
                elif resp.status_code == 429:
                    # Rate limit — Retry-After 헤더 존재 시 대기
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning(
                        f"텔레그램 Rate Limit, {retry_after}초 대기 "
                        f"(시도 {attempt + 1}/{max_retries})"
                    )
                    time.sleep(retry_after)
                    continue
                else:
                    logger.error(
                        f"텔레그램 발송 실패 ({resp.status_code}): {resp.text}"
                    )
                    return False
            except requests.exceptions.Timeout:
                logger.warning(
                    f"텔레그램 타임아웃 (시도 {attempt + 1}/{max_retries})"
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return False
            except Exception as e:
                err_msg = (
                    str(e).replace(self.token, "***") if self.token else str(e)
                )
                logger.error(f"텔레그램 오류: {err_msg}")
                return False
        return False

    def _send_chunked(self, message: str, parse_mode: str) -> bool:
        """4096자 초과 메시지를 분할 발송

        Args:
            message: 긴 메시지
            parse_mode: 파싱 모드

        Returns:
            True=전체 성공, False=하나라도 실패
        """
        chunks = []
        while message:
            if len(message) <= MAX_MESSAGE_LENGTH:
                chunks.append(message)
                break
            cut_pos = message.rfind("\n", 0, MAX_MESSAGE_LENGTH)
            if cut_pos == -1:
                cut_pos = MAX_MESSAGE_LENGTH
            chunks.append(message[:cut_pos])
            message = message[cut_pos:].lstrip("\n")

        success = True
        for chunk in chunks:
            if not self._send_single(chunk, parse_mode):
                success = False
        return success

    def send_rebalancing_report(
        self,
        sell_done: list[str],
        buy_done: list[str],
        total_value: float,
        sell_total: int = 0,
        buy_total: int = 0,
    ) -> bool:
        """월별 리밸런싱 결과 알림

        Args:
            sell_done: 매도 완료 종목 리스트
            buy_done: 매수 완료 종목 리스트
            total_value: 총 평가금액
            sell_total: 매도 계획 수
            buy_total: 매수 계획 수

        Returns:
            발송 성공 여부
        """
        sell_preview = ", ".join(sell_done[:5])
        if len(sell_done) > 5:
            sell_preview += " ..."
        buy_preview = ", ".join(buy_done[:5])
        if len(buy_done) > 5:
            buy_preview += " ..."

        msg = (
            f"*월별 리밸런싱 완료*\n\n"
            f"매도: {len(sell_done)}/{sell_total or len(sell_done)}개\n"
            f"`{sell_preview}`\n\n"
            f"매수: {len(buy_done)}/{buy_total or len(buy_done)}개\n"
            f"`{buy_preview}`\n\n"
            f"총 평가금액: {total_value:,.0f}원"
        )
        return self.send(msg)

    def send_daily_report(self, daily_return: float, total_value: float) -> bool:
        """일별 수익 리포트

        Args:
            daily_return: 당일 수익률 (소수점, 예: 0.015 = 1.5%)
            total_value: 총 평가금액

        Returns:
            발송 성공 여부
        """
        msg = (
            f"*일별 리포트*\n\n"
            f"당일 수익률: `{daily_return * 100:+.2f}%`\n"
            f"총 평가금액: `{total_value:,.0f}원`"
        )
        return self.send(msg)

    def send_error(self, error_message: str) -> bool:
        """오류 알림

        Args:
            error_message: 오류 메시지 (500자까지만 포함)

        Returns:
            발송 성공 여부
        """
        msg = f"*오류 발생*\n\n```\n{error_message[:500]}\n```"
        return self.send(msg)
