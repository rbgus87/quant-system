# trading/order.py
import json
import logging
import time
from pathlib import Path
from trading.kiwoom_api import KiwoomRestClient
from config.settings import settings

logger = logging.getLogger(__name__)


class BalanceValidationError(Exception):
    """잔고 조회 결과가 비정상일 때 발생"""


class TurnoverLimitExceeded(Exception):
    """턴오버 상한 초과 시 발생"""


class DrawdownCircuitBreaker(Exception):
    """MDD 서킷 브레이커 발동 시 발생"""


class OrderExecutor:
    """리밸런싱 주문 실행기

    순서: 매도(예수금 확보) → 체결 확인 → 잔고 재확인(99% 사용) → 매수(동일 비중)

    안전장치:
    - 잔고 조회 실패 시 abort (BalanceValidationError)
    - 단일 종목 최대 비중 제한 (max_position_pct)
    - 월간 교체율 상한 (max_turnover_pct)
    - 매도 체결 확인: get_unfilled_orders() 기반
    """

    _PEAK_VALUE_PATH = Path("data/peak_value.json")

    def __init__(self, initial_value: float = 0) -> None:
        self.api = KiwoomRestClient()
        self.cfg = settings.trading
        self._peak_value: float = self._load_peak_value(initial_value)

        if not settings.is_paper_trading:
            logger.warning(
                "OrderExecutor 실전투자 모드로 초기화됨 — "
                "IS_PAPER_TRADING=false 확인 필요"
            )

    def _load_peak_value(self, default: float) -> float:
        """영속화된 고점 값 로드"""
        try:
            if self._PEAK_VALUE_PATH.exists():
                with open(self._PEAK_VALUE_PATH, "r") as f:
                    data = json.load(f)
                val = float(data.get("peak_value", default))
                if val > 0:
                    logger.info(f"MDD 고점 복원: {val:,.0f}원")
                    return val
        except Exception as e:
            logger.warning(f"MDD 고점 로드 실패: {e}")
        return default

    def _save_peak_value(self) -> None:
        """고점 값 영속화"""
        try:
            self._PEAK_VALUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self._PEAK_VALUE_PATH, "w") as f:
                json.dump({"peak_value": self._peak_value}, f)
        except Exception as e:
            logger.error(f"MDD 고점 저장 실패: {e}")

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

    def _validate_balance(self, balance: dict, context: str = "") -> None:
        """잔고 조회 결과 검증 — 비정상 시 abort

        Args:
            balance: get_balance() 반환값
            context: 로그용 컨텍스트 설명

        Raises:
            BalanceValidationError: 잔고 조회 실패 또는 비정상 데이터
        """
        cash = balance.get("cash", 0)
        total = balance.get("total_eval_amount", 0)
        holdings = balance.get("holdings", [])

        # 보유 종목이 있는데 총평가가 0이면 API 실패로 간주
        if holdings and total <= 0:
            raise BalanceValidationError(
                f"잔고 검증 실패 [{context}]: 보유 종목 {len(holdings)}개인데 "
                f"총평가 {total}원. API 응답 이상."
            )

        # 보유 종목이 없는데 현금도 0이고 총평가도 0이면 API 실패 가능성
        if not holdings and cash == 0 and total == 0:
            logger.warning(
                f"잔고 검증 경고 [{context}]: 보유 종목 0, 현금 0, 총평가 0 — "
                f"계좌가 비어있거나 API 실패일 수 있음"
            )

    def _check_turnover_limit(
        self,
        sell_count: int,
        current_count: int,
    ) -> None:
        """턴오버 제한 검증

        Args:
            sell_count: 매도 예정 종목 수
            current_count: 현재 보유 종목 수

        Raises:
            TurnoverLimitExceeded: 교체율 상한 초과
        """
        if current_count == 0:
            return

        turnover = sell_count / current_count
        max_turnover = self.cfg.max_turnover_pct

        if turnover > max_turnover:
            raise TurnoverLimitExceeded(
                f"턴오버 제한 초과: {turnover:.0%} > {max_turnover:.0%} "
                f"(매도 {sell_count}/{current_count}개). "
                f"스크리너 데이터 이상 가능성 — 리밸런싱 중단."
            )

    def _check_drawdown(self, total_value: float) -> None:
        """MDD 서킷 브레이커 — 고점 대비 하락폭 검증

        Args:
            total_value: 현재 총 평가 금액

        Raises:
            DrawdownCircuitBreaker: MDD 서킷 브레이커 발동
        """
        if total_value <= 0:
            return

        # 고점 갱신
        if total_value > self._peak_value:
            self._peak_value = total_value
            self._save_peak_value()

        if self._peak_value <= 0:
            return

        drawdown = (total_value - self._peak_value) / self._peak_value
        max_dd = self.cfg.max_drawdown_pct

        if drawdown < -max_dd:
            raise DrawdownCircuitBreaker(
                f"MDD 서킷 브레이커: {drawdown:.1%} < -{max_dd:.0%} "
                f"(현재 {total_value:,.0f}원, 고점 {self._peak_value:,.0f}원). "
                f"리밸런싱 중단."
            )

    def execute_rebalancing(
        self,
        current_holdings: list[str],
        target_portfolio: list[str],
    ) -> tuple[list[str], list[str]]:
        """리밸런싱 주문 실행

        순서: 매도 → 체결 확인 → 잔고 재확인 → 매수 (동일 비중)

        Args:
            current_holdings: 현재 보유 종목 코드 리스트
            target_portfolio: 목표 포트폴리오 코드 리스트

        Returns:
            (매도 완료 리스트, 매수 완료 리스트)
        """
        exchange = "KRX" if self.api.is_paper else "SOR"

        sell_list, buy_list = self._calculate_orders(current_holdings, target_portfolio)
        logger.info(f"리밸런싱 계획: 매도 {len(sell_list)}개, 매수 {len(buy_list)}개")

        # 턴오버 제한 검증 (전량 청산은 의도적 행위이므로 예외)
        if target_portfolio:
            self._check_turnover_limit(len(sell_list), len(current_holdings))
        elif current_holdings:
            logger.warning(
                f"전량 청산 모드: 목표 포트폴리오 비어있음, "
                f"보유 {len(current_holdings)}개 종목 전부 매도 예정"
            )

        sell_done: list[str] = []
        buy_done: list[str] = []
        sell_order_nos: list[str] = []

        # ① 매도 먼저 (예수금 확보)
        balance = self.api.get_balance()
        self._validate_balance(balance, "매도 전")

        # MDD 서킷 브레이커
        total_eval = balance.get("total_eval_amount", 0)
        if total_eval > 0:
            self._check_drawdown(total_eval)

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
                    ord_no = result.get("ord_no", "")
                    if ord_no:
                        sell_order_nos.append(ord_no)
            else:
                logger.warning(f"잔고에 {ticker} 없음, 매도 스킵")

        # ② 매도 체결 확인 (주문번호 기반)
        if sell_done:
            try:
                self._wait_for_sells_to_settle(sell_done, sell_order_nos)
            except TimeoutError:
                logger.warning(
                    f"매도 미체결로 매수 중단 — 매도 완료: {len(sell_done)}/{len(sell_list)}"
                )
                return sell_done, buy_done

        updated_balance = self.api.get_balance()
        self._validate_balance(updated_balance, "매수 전")
        available_cash = updated_balance.get("cash", 0) * 0.99
        total_eval = updated_balance.get("total_eval_amount", 0)

        # 고정 금액 모드: 매수 예산 상한 적용
        max_inv = settings.portfolio.max_investment_amount
        if max_inv > 0 and available_cash > max_inv:
            logger.info(
                f"고정 금액 모드: 예수금 {available_cash:,.0f}원 중 "
                f"{max_inv:,.0f}원만 매수에 사용"
            )
            available_cash = max_inv

        if not buy_list:
            logger.info(
                f"리밸런싱 완료 — 매도: {len(sell_done)}/{len(sell_list)}, "
                f"매수: 0/0"
            )
            return sell_done, buy_done

        # 현재가 조회 → 매수 가능 종목 필터링 (반복적 재분배)
        buy_prices: dict[str, float] = {}
        for ticker in buy_list:
            price_data = self.api.get_current_price(ticker)
            price = price_data.get("current_price", 0)
            if price > 0:
                buy_prices[ticker] = price
            else:
                logger.warning(f"현재가 조회 실패, 매수 스킵: {ticker}")

        affordable_list = list(buy_prices.keys())
        cost_rate = 1 + self.cfg.commission_rate + self.cfg.slippage

        # 반복적 재분배: 1주도 못 사는 종목 제외 → 남은 종목에 재분배
        excluded: list[str] = []
        while affordable_list:
            n_buy = len(affordable_list)
            budget_per_stock = available_cash / n_buy

            # 단일 종목 최대 비중 제한
            max_position_amount = (
                total_eval * self.cfg.max_position_pct
                if total_eval > 0
                else budget_per_stock
            )
            budget_per_stock = min(budget_per_stock, max_position_amount)

            # 1주도 못 사는 종목 찾기
            unaffordable = [
                t for t in affordable_list
                if int(budget_per_stock / (buy_prices[t] * cost_rate)) <= 0
            ]

            if not unaffordable:
                break  # 모든 종목 매수 가능

            for t in unaffordable:
                logger.warning(
                    f"자본 부족으로 제외: {t} "
                    f"(가격 {buy_prices[t]:,.0f}원 > 종목당 배분 {budget_per_stock:,.0f}원)"
                )
                affordable_list.remove(t)
                excluded.append(t)

        if excluded:
            logger.info(
                f"자본 적응형 배분: {len(excluded)}개 제외, "
                f"{len(affordable_list)}개 종목에 재분배"
            )

        # ③ 매수 (동일 비중, 재분배 완료된 목록)
        for ticker in affordable_list:
            price = buy_prices[ticker]
            qty = int(budget_per_stock / (price * cost_rate))
            if qty <= 0:
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
            f"매수: {len(buy_done)}/{len(affordable_list)}"
            + (f" (자본부족 제외: {len(excluded)}개)" if excluded else "")
        )
        return sell_done, buy_done

    def _wait_for_sells_to_settle(
        self,
        sold_tickers: list[str],
        order_nos: list[str],
        max_wait_sec: int = 30,
        poll_interval: int = 3,
    ) -> None:
        """매도 주문 체결 대기 (주문번호 기반 + 잔고 폴백)

        Args:
            sold_tickers: 매도 주문 완료된 종목 리스트
            order_nos: 매도 주문번호 리스트
            max_wait_sec: 최대 대기 시간 (초)
            poll_interval: 확인 간격 (초)
        """
        elapsed = 0
        while elapsed < max_wait_sec:
            time.sleep(poll_interval)
            elapsed += poll_interval

            # 주문번호 기반 확인 (우선)
            if order_nos:
                unfilled = self.api.get_unfilled_orders()
                unfilled_nos = {
                    o.get("ord_no", "") for o in unfilled
                }
                remaining_orders = [no for no in order_nos if no in unfilled_nos]
                if not remaining_orders:
                    logger.info(f"매도 체결 완료 — 주문번호 확인 ({elapsed}초 소요)")
                    return
                logger.info(
                    f"매도 체결 대기 중... 미체결 {len(remaining_orders)}건 ({elapsed}s/{max_wait_sec}s)"
                )
            else:
                # 주문번호 없으면 잔고 기반 폴백
                balance = self.api.get_balance()
                held_tickers = {h["ticker"] for h in balance.get("holdings", [])}
                remaining = set(sold_tickers) & held_tickers
                if not remaining:
                    logger.info(f"매도 체결 완료 — 잔고 확인 ({elapsed}초 소요)")
                    return
                logger.info(
                    f"매도 체결 대기 중... 미체결 {len(remaining)}건 ({elapsed}s/{max_wait_sec}s)"
                )

        msg = (
            f"매도 체결 대기 타임아웃 ({max_wait_sec}초). "
            f"미체결 종목이 있을 수 있어 매수를 중단합니다."
        )
        logger.warning(msg)
        raise TimeoutError(msg)
