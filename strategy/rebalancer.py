# strategy/rebalancer.py
import logging
from config.settings import settings

logger = logging.getLogger(__name__)


class Rebalancer:
    """포트폴리오 리밸런싱 주문 생성기

    현재 포트폴리오와 목표 포트폴리오를 비교하여
    매도/매수 주문 목록을 생성하고 거래 비용을 계산한다.
    """

    def __init__(self) -> None:
        self.cfg = settings.trading

    def compute_orders(
        self,
        current_holdings: dict[str, int],
        target_tickers: list[str],
    ) -> tuple[list[str], list[str]]:
        """현재 vs 목표 포트폴리오 비교하여 매도/매수 목록 생성

        Args:
            current_holdings: 현재 보유 종목 {ticker: shares}
            target_tickers: 목표 종목 코드 리스트

        Returns:
            (sell_tickers, buy_tickers) 튜플
        """
        current_set = set(current_holdings.keys())
        target_set = set(target_tickers)

        sell_tickers = sorted(current_set - target_set)
        buy_tickers = sorted(target_set - current_set)

        if sell_tickers:
            logger.info(f"매도 종목: {len(sell_tickers)}개")
        if buy_tickers:
            logger.info(f"매수 종목: {len(buy_tickers)}개")

        return sell_tickers, buy_tickers

    def calc_sell_proceed(self, price: float, shares: int) -> float:
        """매도 수익금 계산 (수수료 + 세금 + 슬리피지 차감)

        Args:
            price: 체결 가격 (시가)
            shares: 매도 주식 수

        Returns:
            실수령 금액
        """
        gross = price * shares
        cost_rate = self.cfg.commission_rate + self.cfg.tax_rate + self.cfg.slippage
        return gross * (1 - cost_rate)

    def calc_buy_cost(self, price: float, shares: int) -> float:
        """매수 총 비용 계산 (수수료 + 슬리피지 추가)

        Args:
            price: 체결 가격 (시가)
            shares: 매수 주식 수

        Returns:
            총 지출 금액
        """
        gross = price * shares
        cost_rate = self.cfg.commission_rate + self.cfg.slippage
        return gross * (1 + cost_rate)

    def calc_buy_shares(self, target_amount: float, price: float) -> int:
        """목표 금액으로 매수 가능한 주식 수 계산

        Args:
            target_amount: 목표 투자 금액
            price: 체결 가격 (시가)

        Returns:
            매수 가능 주식 수 (정수, 내림)
        """
        cost_rate = self.cfg.commission_rate + self.cfg.slippage
        effective_price = price * (1 + cost_rate)
        return int(target_amount / effective_price)
