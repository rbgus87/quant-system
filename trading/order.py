# trading/order.py
import json
import logging
import time
from datetime import date
from pathlib import Path
from trading.kiwoom_api import KiwoomRestClient
from config.settings import settings
from data.storage import DataStorage

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

    def __init__(self, initial_value: float = 0) -> None:
        self.api = KiwoomRestClient()
        self.cfg = settings.trading
        self.storage = DataStorage()
        mode = "paper" if settings.is_paper_trading else "live"
        self._state_path = Path(f"data/peak_value_{mode}.json")
        self._peak_value: float = self._load_peak_value(initial_value)
        self._circuit_breaker_active: bool = self._load_cb_state()

        if not settings.is_paper_trading:
            logger.warning(
                "OrderExecutor 실전투자 모드로 초기화됨 — "
                "IS_PAPER_TRADING=false 확인 필요"
            )

    def _load_state(self) -> dict:
        """영속화된 상태 파일 로드"""
        try:
            if self._state_path.exists():
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"상태 파일 로드 실패: {e}")
        return {}

    def _save_state(self, updates: dict) -> None:
        """상태 파일에 값 저장"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            existing = self._load_state()
            existing.update(updates)
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(existing, f)
        except Exception as e:
            logger.error(f"상태 파일 저장 실패: {e}")

    def _load_peak_value(self, default: float) -> float:
        """영속화된 고점 값 로드"""
        data = self._load_state()
        val = float(data.get("peak_value", default))
        if val > 0:
            logger.info(f"MDD 고점 복원: {val:,.0f}원")
            return val
        return default

    def _save_peak_value(self) -> None:
        """고점 값 영속화"""
        self._save_state({"peak_value": self._peak_value})

    def _load_cb_state(self) -> bool:
        """서킷브레이커 활성 상태 로드"""
        data = self._load_state()
        active = data.get("circuit_breaker_active", False)
        if active:
            logger.warning("서킷브레이커 활성 상태 복원 — 재진입 조건 확인 필요")
        return active

    def _save_cb_state(self, active: bool) -> None:
        """서킷브레이커 상태 영속화"""
        self._circuit_breaker_active = active
        self._save_state({"circuit_breaker_active": active})

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

    def _check_drawdown(self, total_value: float) -> bool:
        """MDD 서킷 브레이커 — 고점 대비 하락폭 검증

        Args:
            total_value: 현재 총 평가 금액

        Returns:
            True = 서킷브레이커 발동 (전량 매도 필요), False = 정상
        """
        if total_value <= 0:
            return False

        # 고점 갱신
        if total_value > self._peak_value:
            self._peak_value = total_value
            self._save_peak_value()

        if self._peak_value <= 0:
            return False

        max_dd = self.cfg.max_drawdown_pct
        if not max_dd or max_dd <= 0:
            return False

        drawdown = (total_value - self._peak_value) / self._peak_value
        if drawdown < -max_dd:
            logger.warning(
                f"MDD 서킷 브레이커 발동: {drawdown:.1%} < -{max_dd:.0%} "
                f"(현재 {total_value:,.0f}원, 고점 {self._peak_value:,.0f}원) "
                f"→ 전량 매도 후 현금 대피"
            )
            return True
        return False

    def check_circuit_breaker_reentry(self, total_value: float) -> bool:
        """서킷브레이커 재진입 조건 확인

        고점 대비 DD가 발동 기준의 절반 이내로 회복하면 재진입 허용.

        Args:
            total_value: 현재 총 평가 금액

        Returns:
            True = 재진입 허용 (서킷브레이커 해제), False = 계속 현금 유지
        """
        if not self._circuit_breaker_active:
            return True

        if self._peak_value <= 0 or total_value <= 0:
            return False

        max_dd = self.cfg.max_drawdown_pct
        if not max_dd or max_dd <= 0:
            return True

        drawdown = (total_value - self._peak_value) / self._peak_value
        reentry_threshold = -max_dd * 0.5

        if drawdown >= reentry_threshold:
            logger.info(
                f"서킷브레이커 해제: DD={drawdown:.1%} >= {reentry_threshold:.1%} → 재진입 허용"
            )
            self._peak_value = total_value
            self._save_peak_value()
            self._save_cb_state(False)
            return True

        logger.warning(
            f"서킷브레이커 유지: DD={drawdown:.1%} (해제 기준: {reentry_threshold:.1%})"
        )
        return False

    def execute_emergency_liquidation(self) -> list[str]:
        """서킷브레이커 전량 매도 — 모든 보유종목 시장가 매도

        Returns:
            매도 완료 종목 코드 리스트
        """
        exchange = "KRX" if self.api.is_paper else "SOR"
        balance = self.api.get_balance()
        self._validate_balance(balance, "서킷브레이커 전량 매도")

        sell_done: list[str] = []
        sell_order_nos: list[str] = []

        for holding in balance.get("holdings", []):
            ticker = holding.get("ticker", "")
            qty = holding.get("qty", 0)
            if qty <= 0:
                continue

            result = self.api.sell_stock(
                ticker=ticker, qty=qty, order_type="3", exchange=exchange,
            )
            if result.get("return_code") == 0:
                sell_done.append(ticker)
                ord_no = result.get("ord_no", "")
                if ord_no:
                    sell_order_nos.append(ord_no)
                # DB 기록
                price = holding.get("current_price", 0)
                amount = price * qty
                self.storage.save_trade(
                    trade_date=date.today(),
                    ticker=ticker,
                    side="SELL",
                    quantity=qty,
                    price=price,
                    amount=amount,
                    commission=amount * self.cfg.commission_rate,
                    tax=amount * self.cfg.tax_rate,
                    is_paper=settings.is_paper_trading,
                )

        if sell_done:
            try:
                self._wait_for_sells_to_settle(sell_done, sell_order_nos)
            except TimeoutError:
                logger.warning("서킷브레이커 매도 일부 미체결 가능성")

        self._save_cb_state(True)
        logger.warning(
            f"서킷브레이커 전량 매도 완료: {len(sell_done)}종목 청산"
        )
        return sell_done

    def _check_trailing_stops(
        self,
        balance: dict,
    ) -> list[str]:
        """보유종목 트레일링 스톱 체크: 매수가 대비 -N% 하락 종목 감지

        Args:
            balance: get_balance() 반환값

        Returns:
            트레일링 스톱 발동 종목 코드 리스트
        """
        if self.cfg.trailing_stop_pct is None:
            logger.debug("트레일링 스톱 비활성화 (null)")
            return []

        trailing_stop_pct = self.cfg.trailing_stop_pct
        if trailing_stop_pct <= 0:
            return []

        stop_tickers: list[str] = []
        for holding in balance.get("holdings", []):
            ticker = holding.get("ticker", "")
            qty = holding.get("qty", 0)
            avg_price = holding.get("avg_price", 0)
            current_price = holding.get("current_price", 0)

            if qty <= 0 or avg_price <= 0 or current_price <= 0:
                continue

            loss_pct = (current_price - avg_price) / avg_price
            if loss_pct < -trailing_stop_pct:
                name = holding.get("name", ticker)
                logger.warning(
                    f"트레일링 스톱 발동: {ticker}({name}) "
                    f"매수가 {avg_price:,.0f}원 → 현재 {current_price:,.0f}원 "
                    f"({loss_pct:.1%} < -{trailing_stop_pct:.0%})"
                )
                stop_tickers.append(ticker)

        if stop_tickers:
            logger.warning(
                f"트레일링 스톱 대상: {len(stop_tickers)}종목 강제 매도 예정"
            )
        return stop_tickers

    def execute_rebalancing(
        self,
        current_holdings: list[str],
        target_portfolio: list[str],
        invest_ratio: float = 1.0,
        skip_turnover_check: bool = False,
    ) -> tuple[list[str], list[str]]:
        """리밸런싱 주문 실행

        순서: 트레일링 스톱 체크 → MDD 체크 → 매도 → 체결 확인 → 잔고 재확인 → 매수 (동일 비중)

        Args:
            current_holdings: 현재 보유 종목 코드 리스트
            target_portfolio: 목표 포트폴리오 코드 리스트
            invest_ratio: 투자 비중 (0.0~1.0, 시장 레짐/변동성 타겟팅 반영)
            skip_turnover_check: 턴오버 제한 검증 건너뛰기 (수동 리밸런싱 시)

        Returns:
            (매도 완료 리스트, 매수 완료 리스트)
        """
        exchange = "KRX" if self.api.is_paper else "SOR"

        # ── 트레일링 스톱: 매수가 대비 -N% 하락 종목 강제 매도 ──
        pre_balance = self.api.get_balance()
        self._validate_balance(pre_balance, "트레일링 스톱 체크")
        if self.cfg.trailing_stop_pct is not None:
            stop_tickers = self._check_trailing_stops(pre_balance)
        else:
            stop_tickers = []

        if stop_tickers:
            # 스톱 발동 종목을 target에서 제거 (재매수 방지)
            target_portfolio = [t for t in target_portfolio if t not in stop_tickers]
            # 스톱 발동 종목이 current_holdings에 없으면 추가 (매도 대상에 포함)
            for t in stop_tickers:
                if t not in current_holdings:
                    current_holdings.append(t)

        sell_list, buy_list = self._calculate_orders(current_holdings, target_portfolio)
        logger.info(f"리밸런싱 계획: 매도 {len(sell_list)}개, 매수 {len(buy_list)}개")

        # 턴오버 제한 검증 (전량 청산·수동 리밸런싱은 의도적 행위이므로 예외)
        if target_portfolio and not skip_turnover_check:
            self._check_turnover_limit(len(sell_list), len(current_holdings))
        elif skip_turnover_check and len(sell_list) > 0:
            logger.warning(
                f"턴오버 제한 검증 건너뜀 (수동 리밸런싱): "
                f"매도 {len(sell_list)}/{len(current_holdings)}개"
            )
        elif current_holdings:
            logger.warning(
                f"전량 청산 모드: 목표 포트폴리오 비어있음, "
                f"보유 {len(current_holdings)}개 종목 전부 매도 예정"
            )

        sell_done: list[str] = []
        buy_done: list[str] = []
        sell_order_nos: list[str] = []
        buy_order_nos: list[str] = []

        # ① 매도 먼저 (예수금 확보)
        balance = self.api.get_balance()
        self._validate_balance(balance, "매도 전")

        # MDD 서킷 브레이커: 고점 대비 하락폭 초과 → 전량 매도 후 종료
        total_eval = balance.get("total_eval_amount", 0)
        if total_eval > 0 and self._check_drawdown(total_eval):
            sold = self.execute_emergency_liquidation()
            return sold, []

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
                    # 거래 이력 DB 저장
                    sell_price = holding.get("current_price", 0)
                    sell_qty = holding["qty"]
                    sell_amount = sell_price * sell_qty
                    self.storage.save_trade(
                        trade_date=date.today(),
                        ticker=ticker,
                        side="SELL",
                        quantity=sell_qty,
                        price=sell_price,
                        amount=sell_amount,
                        commission=sell_amount * self.cfg.commission_rate,
                        tax=sell_amount * self.cfg.tax_rate,
                        is_paper=settings.is_paper_trading,
                    )
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

        # 시장 레짐 / 변동성 타겟팅 반영: 투자 비중만큼만 매수
        if 0.0 < invest_ratio < 1.0:
            scaled_cash = available_cash * invest_ratio
            logger.info(
                f"투자 비중 적용: {invest_ratio:.0%} → "
                f"매수 예산 {available_cash:,.0f}원 → {scaled_cash:,.0f}원"
            )
            available_cash = scaled_cash

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
        MAX_BUY_QTY = 10000  # 단일 종목 매수 수량 상한 (안전장치)
        for ticker in affordable_list:
            price = buy_prices[ticker]
            qty = int(budget_per_stock / (price * cost_rate))
            if qty <= 0:
                continue

            if qty > MAX_BUY_QTY:
                logger.warning(
                    f"매수 수량 상한 적용: {ticker} {qty}주 → {MAX_BUY_QTY}주"
                )
                qty = MAX_BUY_QTY

            result = self.api.buy_stock(
                ticker=ticker,
                qty=qty,
                order_type="3",
                exchange=exchange,
            )
            if result.get("return_code") == 0:
                buy_done.append(ticker)
                ord_no = result.get("ord_no", "")
                if ord_no:
                    buy_order_nos.append(ord_no)
                # 거래 이력 DB 저장
                buy_amount = price * qty
                self.storage.save_trade(
                    trade_date=date.today(),
                    ticker=ticker,
                    side="BUY",
                    quantity=qty,
                    price=price,
                    amount=buy_amount,
                    commission=buy_amount * self.cfg.commission_rate,
                    tax=0.0,  # 매수 시 거래세 없음
                    is_paper=settings.is_paper_trading,
                )

        # ④ 실패분 재시도 (rate limit 등 일시적 실패 대응)
        # 중복 주문 방지: 미체결 주문 조회 후 이미 접수된 종목은 스킵
        failed_tickers = [
            t for t in affordable_list
            if t not in buy_done and t in buy_prices
        ]
        if failed_tickers:
            logger.info(
                f"매수 실패 {len(failed_tickers)}종목 재시도 대기 (5초)..."
            )
            time.sleep(5)
            # 미체결 조회로 이미 접수된 주문 확인 (중복 주문 방지)
            try:
                unfilled = self.api.get_unfilled_orders()
                unfilled_tickers = {
                    o.get("stk_cd", o.get("ticker", ""))
                    for o in unfilled
                }
            except Exception:
                unfilled_tickers = set()
                logger.warning("미체결 조회 실패 — 재시도 전체 스킵 (중복 주문 방지)")
                failed_tickers = []

            for ticker in failed_tickers:
                if ticker in unfilled_tickers:
                    logger.info(f"미체결 주문 존재, 재시도 스킵: {ticker}")
                    continue
                price = buy_prices[ticker]
                qty = int(budget_per_stock / (price * cost_rate))
                if qty <= 0:
                    continue
                if qty > MAX_BUY_QTY:
                    qty = MAX_BUY_QTY
                result = self.api.buy_stock(
                    ticker=ticker,
                    qty=qty,
                    order_type="3",
                    exchange=exchange,
                )
                if result.get("return_code") == 0:
                    buy_done.append(ticker)
                    ord_no = result.get("ord_no", "")
                    if ord_no:
                        buy_order_nos.append(ord_no)
                    buy_amount = price * qty
                    self.storage.save_trade(
                        trade_date=date.today(),
                        ticker=ticker,
                        side="BUY",
                        quantity=qty,
                        price=price,
                        amount=buy_amount,
                        commission=buy_amount * self.cfg.commission_rate,
                        tax=0.0,
                        is_paper=settings.is_paper_trading,
                    )
                    logger.info(f"재시도 매수 성공: {ticker} {qty}주")
                else:
                    logger.warning(f"재시도 매수 실패: {ticker}")

        # ⑤ 매수 체결 확인 — 미체결 종목은 buy_done에서 제외 + 경고 + DB 실패 기록
        if buy_done and buy_order_nos:
            unfilled_buys = self._wait_for_buys_to_settle(buy_done, buy_order_nos)
            if unfilled_buys:
                try:
                    from notify.telegram import TelegramNotifier
                    notifier = TelegramNotifier()
                    notifier.send_error(
                        f"매수 미체결 {len(unfilled_buys)}종목: {unfilled_buys} — "
                        f"수동 확인 필요"
                    )
                except Exception as e:
                    logger.warning(f"텔레그램 발송 실패: {e}")

                for ticker in unfilled_buys:
                    try:
                        self.storage.save_trade(
                            trade_date=date.today(),
                            ticker=ticker,
                            side="BUY",
                            quantity=0,  # 0 = 체결 실패 마커
                            price=0,
                            amount=0,
                            commission=0,
                            tax=0,
                            is_paper=settings.is_paper_trading,
                        )
                    except Exception as e:
                        logger.warning(f"체결 실패 기록 저장 실패: {e}")

                buy_done = [t for t in buy_done if t not in unfilled_buys]

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
        max_wait_sec: int = 60,
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

    def _wait_for_buys_to_settle(
        self,
        bought_tickers: list[str],
        order_nos: list[str],
        max_wait_sec: int = 120,
        poll_interval: int = 3,
    ) -> list[str]:
        """매수 주문 체결 대기 (주문번호 기반 + 잔고 폴백)

        매도와 달리 타임아웃 시 예외를 발생시키지 않고,
        미체결 종목 리스트를 반환하여 호출자가 buy_done에서 제외하도록 함.

        Args:
            bought_tickers: 매수 주문 완료된 종목 리스트
            order_nos: 매수 주문번호 리스트
            max_wait_sec: 최대 대기 시간 (초, 기본 120 - 매수는 매도보다 오래 걸림)
            poll_interval: 확인 간격 (초)

        Returns:
            완전 미체결 종목 코드 리스트 (빈 리스트 = 전부 체결)
        """
        elapsed = 0
        while elapsed < max_wait_sec:
            time.sleep(poll_interval)
            elapsed += poll_interval

            if order_nos:
                unfilled = self.api.get_unfilled_orders()
                unfilled_nos = {o.get("ord_no", "") for o in unfilled}
                remaining_orders = [no for no in order_nos if no in unfilled_nos]
                if not remaining_orders:
                    logger.info(f"매수 체결 완료 — 주문번호 확인 ({elapsed}초 소요)")
                    return []
                logger.info(
                    f"매수 체결 대기 중... 미체결 {len(remaining_orders)}건 "
                    f"({elapsed}s/{max_wait_sec}s)"
                )
            else:
                balance = self.api.get_balance()
                held_tickers = {h["ticker"] for h in balance.get("holdings", [])}
                remaining = set(bought_tickers) - held_tickers
                if not remaining:
                    logger.info(f"매수 체결 완료 — 잔고 확인 ({elapsed}초 소요)")
                    return []
                logger.info(
                    f"매수 체결 대기 중... 미체결 {len(remaining)}건 "
                    f"({elapsed}s/{max_wait_sec}s)"
                )

        # 타임아웃 — 미체결 종목 식별
        unfilled_tickers: list[str] = []
        try:
            unfilled = self.api.get_unfilled_orders()
            unfilled_map = {
                o.get("ord_no", ""): o.get("stk_cd", o.get("ticker", ""))
                for o in unfilled
            }
            for no in order_nos:
                t = unfilled_map.get(no)
                if t:
                    unfilled_tickers.append(t)
        except Exception as e:
            logger.warning(f"미체결 조회 실패: {e} — 잔고 기반 추정")
            try:
                balance = self.api.get_balance()
                held = {h["ticker"] for h in balance.get("holdings", [])}
                unfilled_tickers = [t for t in bought_tickers if t not in held]
            except Exception:
                unfilled_tickers = []

        if unfilled_tickers:
            logger.warning(
                f"매수 체결 대기 타임아웃 ({max_wait_sec}초) — "
                f"미체결 {len(unfilled_tickers)}종목: {unfilled_tickers}"
            )
        return unfilled_tickers
