# trading/order.py
import logging
from trading.kiwoom_api import KiwoomRestClient
from config.settings import settings

logger = logging.getLogger(__name__)


class OrderExecutor:
    """리밸런싱 주문 실행기

    순서: 매도(예수금 확보) → 잔고 재확인(99% 사용) → 매수(동일 비중)
    """

    def __init__(self) -> None:
        self.api = KiwoomRestClient()
        self.cfg = settings.trading

        if not settings.is_paper_trading:
            logger.warning(
                "OrderExecutor 실전투자 모드로 초기화됨 — "
                "IS_PAPER_TRADING=false 확인 필요"
            )

    def _calculate_orders(
        self,
        current_holdings: list[str],
        target_portfolio: list[str],
    ) -> tuple[list[str], list[str]]:
        """매수/매도 종목 계산

        Args:
            current_holdings: 현재 보유 종목 코드 리스트
            target_portfolio: 목표 포트폴리오 코드 리스트

        Returns:
            (sell_tickers, buy_tickers) 튜플
        """
        current_set = set(current_holdings)
        target_set = set(target_portfolio)

        sell_tickers = sorted(current_set - target_set)
        buy_tickers = sorted(target_set - current_set)

        logger.info(f"주문 계산: 매도 {len(sell_tickers)}개, 매수 {len(buy_tickers)}개")
        return sell_tickers, buy_tickers

    def execute_rebalancing(
        self,
        current_holdings: list[str],
        target_portfolio: list[str],
    ) -> tuple[list[str], list[str]]:
        """리밸런싱 주문 실행

        순서: 매도 → 잔고 재확인 → 매수 (동일 비중)

        Args:
            current_holdings: 현재 보유 종목 코드 리스트
            target_portfolio: 목표 포트폴리오 코드 리스트

        Returns:
            (매도 완료 리스트, 매수 완료 리스트)
        """
        exchange = "KRX" if self.api.is_paper else "SOR"

        sell_list, buy_list = self._calculate_orders(current_holdings, target_portfolio)
        logger.info(f"리밸런싱 계획: 매도 {len(sell_list)}개, 매수 {len(buy_list)}개")

        sell_done: list[str] = []
        buy_done: list[str] = []

        # ① 매도 먼저 (예수금 확보)
        balance = self.api.get_balance()
        for ticker in sell_list:
            holding = next(
                (h for h in balance["holdings"] if h["ticker"] == ticker),
                None,
            )
            if holding and holding["qty"] > 0:
                result = self.api.sell_stock(
                    ticker=ticker,
                    qty=holding["qty"],
                    order_type="3",
                    exchange=exchange,
                )
                if result.get("return_code") == 0:
                    sell_done.append(ticker)
            else:
                logger.warning(f"잔고에 {ticker} 없음, 매도 스킵")

        # ② 매도 체결 대기 후 예수금 재확인
        if sell_done:
            self._wait_for_sells_to_settle(sell_done)
        updated_balance = self.api.get_balance()
        available_cash = updated_balance.get("cash", 0) * 0.99
        n_buy = len(buy_list)

        if n_buy == 0:
            logger.info(
                f"리밸런싱 완료 — 매도: {len(sell_done)}/{len(sell_list)}, "
                f"매수: 0/0"
            )
            return sell_done, buy_done

        budget_per_stock = available_cash / n_buy

        # ③ 매수 (동일 비중)
        for ticker in buy_list:
            price_data = self.api.get_current_price(ticker)
            price = price_data.get("current_price", 0)
            if price <= 0:
                logger.warning(f"현재가 조회 실패, 매수 스킵: {ticker}")
                continue

            qty = int(
                budget_per_stock
                / (price * (1 + self.cfg.commission_rate + self.cfg.slippage))
            )
            if qty <= 0:
                logger.warning(f"예산 부족, 매수 스킵: {ticker} (가격: {price:,}원)")
                continue

            result = self.api.buy_stock(
                ticker=ticker,
                qty=qty,
                order_type="3",
                exchange=exchange,
            )
            if result.get("return_code") == 0:
                buy_done.append(ticker)

        logger.info(
            f"리밸런싱 완료 — 매도: {len(sell_done)}/{len(sell_list)}, "
            f"매수: {len(buy_done)}/{len(buy_list)}"
        )
        return sell_done, buy_done

    def _wait_for_sells_to_settle(
        self, sold_tickers: list[str], max_wait_sec: int = 30, poll_interval: int = 3
    ) -> None:
        """매도 주문 체결 대기

        Args:
            sold_tickers: 매도 주문 완료된 종목 리스트
            max_wait_sec: 최대 대기 시간 (초)
            poll_interval: 잔고 확인 간격 (초)
        """
        import time

        elapsed = 0
        while elapsed < max_wait_sec:
            time.sleep(poll_interval)
            elapsed += poll_interval
            balance = self.api.get_balance()
            held_tickers = {h["ticker"] for h in balance.get("holdings", [])}
            remaining = set(sold_tickers) & held_tickers
            if not remaining:
                logger.info(f"매도 체결 완료 ({elapsed}초 소요)")
                return
            logger.info(
                f"매도 체결 대기 중... 미체결 {len(remaining)}건 ({elapsed}s/{max_wait_sec}s)"
            )

        logger.warning(
            f"매도 체결 대기 타임아웃 ({max_wait_sec}초). 미체결 종목 있을 수 있음"
        )
