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

    def compute_weight_rebalance(
        self,
        current_holdings: dict[str, int],
        target_tickers: list[str],
        prices: dict[str, float],
        total_value: float,
    ) -> dict[str, int]:
        """전체 목표 포트폴리오에 대한 비중 리밸런싱 주문 계산

        기존 보유종목 포함 모든 목표 종목을 균등 비중으로 조정합니다.
        자본 부족으로 1주도 매수할 수 없는 종목은 자동 제외하고
        남은 종목에 자금을 재분배합니다.

        Args:
            current_holdings: 현재 보유 {ticker: shares}
            target_tickers: 목표 종목 코드 리스트
            prices: 종목별 체결 가격 {ticker: price}
            total_value: 총 포트폴리오 가치

        Returns:
            {ticker: delta_shares} 양수=매수, 음수=매도, 0=변경없음
            포트폴리오에서 빠지는 종목은 -current_shares
        """
        target_set = set(target_tickers)
        if not target_set:
            # 전량 청산
            return {t: -s for t, s in current_holdings.items() if s > 0}

        # 가격 조회 가능한 목표 종목만으로 균등 비중 계산
        priced_targets = [t for t in target_tickers if prices.get(t, 0) > 0]
        n_priced = len(priced_targets)
        if n_priced == 0:
            logger.warning("목표 종목 중 가격 조회 가능한 종목 없음")
            return {}

        # 반복적 재분배: 1주도 못 사는 종목 제외 → 남은 종목에 재분배
        excluded: list[str] = []
        while n_priced > 0:
            target_per_stock = total_value / n_priced
            max_per_stock = total_value * self.cfg.max_position_pct
            if target_per_stock > max_per_stock:
                target_per_stock = max_per_stock

            # 신규 매수 대상 중 1주도 못 사는 종목 찾기
            unaffordable = [
                t for t in priced_targets
                if current_holdings.get(t, 0) == 0
                and self.calc_buy_shares(target_per_stock, prices[t]) == 0
            ]

            if not unaffordable:
                break  # 모든 종목 매수 가능 → 확정

            for t in unaffordable:
                logger.warning(
                    f"자본 부족으로 제외: {t} "
                    f"(주가 {prices[t]:,.0f}원 > 종목당 배분 {target_per_stock:,.0f}원)"
                )
                priced_targets.remove(t)
                excluded.append(t)

            n_priced = len(priced_targets)

        if n_priced == 0:
            logger.warning("모든 목표 종목이 자본 부족으로 제외됨")
            # 기존 보유분 매도만 진행
            orders: dict[str, int] = {}
            for ticker, shares in current_holdings.items():
                if ticker not in target_set and shares > 0:
                    orders[ticker] = -shares
            return orders

        if excluded:
            logger.info(
                f"자본 적응형 배분: {len(excluded)}개 제외, "
                f"{n_priced}개 종목에 재분배 "
                f"(종목당 {target_per_stock:,.0f}원)"
            )

        orders = {}

        # 포트폴리오에서 빠지는 종목: 전량 매도
        for ticker, shares in current_holdings.items():
            if ticker not in target_set and shares > 0:
                orders[ticker] = -shares

        # 목표 종목: 균등 비중으로 조정
        for ticker in priced_targets:
            price = prices[ticker]
            target_shares = self.calc_buy_shares(target_per_stock, price)
            current_shares = current_holdings.get(ticker, 0)
            delta = target_shares - current_shares
            if delta != 0:
                orders[ticker] = delta

        return orders

    def compute_value_weighted_rebalance(
        self,
        current_holdings: dict[str, int],
        target_tickers: list[str],
        prices: dict[str, float],
        total_value: float,
        market_caps: dict[str, float],
    ) -> dict[str, int]:
        """시가총액 가중 리밸런싱 주문 계산

        각 종목의 시가총액 비중으로 목표 비중을 결정합니다.
        max_position_pct로 단일 종목 비중 상한을 적용합니다.

        Args:
            current_holdings: 현재 보유 {ticker: shares}
            target_tickers: 목표 종목 코드 리스트
            prices: 종목별 체결 가격 {ticker: price}
            total_value: 총 포트폴리오 가치
            market_caps: 종목별 시가총액 {ticker: cap}

        Returns:
            {ticker: delta_shares}
        """
        target_set = set(target_tickers)
        if not target_set:
            return {t: -s for t, s in current_holdings.items() if s > 0}

        # 가격 + 시총 모두 있는 종목만
        valid = [
            t for t in target_tickers
            if prices.get(t, 0) > 0 and market_caps.get(t, 0) > 0
        ]
        if not valid:
            return {}

        # 시총 비중 계산 + max_position_pct 상한 적용
        caps = {t: market_caps[t] for t in valid}
        total_cap = sum(caps.values())
        raw_weights = {t: c / total_cap for t, c in caps.items()}

        max_w = self.cfg.max_position_pct
        capped_weights: dict[str, float] = {}
        excess = 0.0
        uncapped: list[str] = []

        for t, w in raw_weights.items():
            if w > max_w:
                capped_weights[t] = max_w
                excess += w - max_w
            else:
                uncapped.append(t)
                capped_weights[t] = w

        # 초과분을 uncapped 종목에 비례 재분배
        if excess > 0 and uncapped:
            uncapped_total = sum(capped_weights[t] for t in uncapped)
            if uncapped_total > 0:
                for t in uncapped:
                    capped_weights[t] += excess * (capped_weights[t] / uncapped_total)

        orders: dict[str, int] = {}

        # 퇴출 종목 전량 매도
        for ticker, shares in current_holdings.items():
            if ticker not in target_set and shares > 0:
                orders[ticker] = -shares

        # 목표 비중대로 주식 수 계산
        for ticker in valid:
            weight = capped_weights.get(ticker, 0)
            target_amount = total_value * weight
            price = prices[ticker]
            target_shares = self.calc_buy_shares(target_amount, price)
            current_shares = current_holdings.get(ticker, 0)
            delta = target_shares - current_shares
            if delta != 0:
                orders[ticker] = delta

        return orders

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

    def estimate_market_impact(
        self,
        order_qty: int,
        avg_daily_volume: float,
        participation_rate: float = 0.1,
    ) -> float:
        """시장 충격 추정 (Square-Root Model)

        Impact ≈ sigma * sqrt(order_qty / (avg_volume * participation_rate))
        간소화: sigma를 1%로 가정

        Args:
            order_qty: 주문 수량
            avg_daily_volume: 20일 평균 거래량
            participation_rate: 참여율 (기본 10%)

        Returns:
            추정 시장 충격 비율 (예: 0.005 = 0.5%)
        """
        if avg_daily_volume <= 0 or order_qty <= 0:
            return 0.0

        sigma = 0.01  # 일일 변동성 1% 가정
        volume_fraction = order_qty / (avg_daily_volume * participation_rate)
        impact = sigma * (volume_fraction ** 0.5)
        return min(float(impact), 0.05)  # 최대 5% 캡

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
