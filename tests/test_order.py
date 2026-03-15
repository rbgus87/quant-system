# tests/test_order.py
import pytest
from unittest.mock import patch, MagicMock

from trading.order import (
    OrderExecutor,
    BalanceValidationError,
    TurnoverLimitExceeded,
)


@pytest.fixture
def mock_api():
    """KiwoomRestClient mock"""
    api = MagicMock()
    api.is_paper = True
    return api


@pytest.fixture
def executor(mock_api, tmp_path):
    """OrderExecutor with mocked API and isolated DB"""
    mock_storage = MagicMock()
    with patch("trading.order.settings") as mock_settings:
        mock_settings.is_paper_trading = True
        mock_settings.trading.commission_rate = 0.00015
        mock_settings.trading.slippage = 0.001
        mock_settings.trading.max_position_pct = 0.10
        mock_settings.trading.max_turnover_pct = 0.50
        mock_settings.trading.max_drawdown_pct = 0.30
        mock_settings.trading.trailing_stop_pct = 0.20
        with patch("trading.order.KiwoomRestClient", return_value=mock_api):
            with patch("trading.order.DataStorage", return_value=mock_storage):
                with patch.object(OrderExecutor, "_load_peak_value", return_value=0):
                    ex = OrderExecutor()
    return ex


def _no_stop_balance(**overrides) -> dict:
    """트레일링 스톱 미발동 잔고 (avg_price 없거나 손실 범위 내)"""
    base = {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0}
    base.update(overrides)
    return base


class TestCalculateOrders:
    """매수/매도 수량 계산 테스트"""

    def test_sell_only(self, executor) -> None:
        """보유 종목 제거 → 매도만"""
        sell, buy = executor._calculate_orders(
            current_holdings=["005930", "000660"],
            target_portfolio=["000660"],
        )
        assert sell == ["005930"]
        assert buy == []

    def test_buy_only(self, executor) -> None:
        """신규 종목 추가 → 매수만"""
        sell, buy = executor._calculate_orders(
            current_holdings=["005930"],
            target_portfolio=["005930", "000660"],
        )
        assert sell == []
        assert buy == ["000660"]

    def test_full_turnover(self, executor) -> None:
        """전체 교체"""
        sell, buy = executor._calculate_orders(
            current_holdings=["005930", "000660"],
            target_portfolio=["035720", "051910"],
        )
        assert set(sell) == {"005930", "000660"}
        assert set(buy) == {"035720", "051910"}

    def test_no_change(self, executor) -> None:
        """변경 없음"""
        sell, buy = executor._calculate_orders(
            current_holdings=["005930"],
            target_portfolio=["005930"],
        )
        assert sell == []
        assert buy == []


class TestBalanceValidation:
    """잔고 검증 안전장치 테스트"""

    def test_valid_balance_passes(self, executor) -> None:
        """정상 잔고 → 검증 통과"""
        balance = {
            "holdings": [{"ticker": "005930", "qty": 100}],
            "cash": 5000000,
            "total_eval_amount": 10000000,
        }
        executor._validate_balance(balance, "테스트")  # 에러 없이 통과

    def test_holdings_with_zero_total_fails(self, executor) -> None:
        """보유 종목이 있는데 총평가 0 → API 실패로 abort"""
        balance = {
            "holdings": [{"ticker": "005930", "qty": 100}],
            "cash": 0,
            "total_eval_amount": 0,
        }
        with pytest.raises(BalanceValidationError, match="총평가.*0원"):
            executor._validate_balance(balance, "테스트")

    def test_empty_account_warning(self, executor) -> None:
        """빈 계좌 → 경고만 (에러 아님)"""
        balance = {
            "holdings": [],
            "cash": 0,
            "total_eval_amount": 0,
        }
        executor._validate_balance(balance, "테스트")  # 경고만, 에러 아님

    def test_balance_validation_in_rebalancing(self, executor, mock_api) -> None:
        """리밸런싱 중 잔고 검증 실패 시 BalanceValidationError 발생"""
        bad_balance = {
            "holdings": [{"ticker": "005930", "qty": 100}, {"ticker": "000660", "qty": 50}],
            "cash": 0,
            "total_eval_amount": 0,
            "total_profit": 0,
        }
        mock_api.get_balance.return_value = bad_balance
        with pytest.raises(BalanceValidationError):
            # 매도 1/2 = 50% → 턴오버 통과, 잔고 검증에서 실패
            executor.execute_rebalancing(
                current_holdings=["005930", "000660"],
                target_portfolio=["000660", "035720"],
            )


class TestTurnoverLimit:
    """턴오버 제한 테스트"""

    def test_within_limit(self, executor) -> None:
        """교체율 50% 이하 → 통과"""
        executor._check_turnover_limit(sell_count=5, current_count=20)

    def test_exceeds_limit(self, executor) -> None:
        """교체율 50% 초과 → TurnoverLimitExceeded"""
        with pytest.raises(TurnoverLimitExceeded, match="턴오버 제한 초과"):
            executor._check_turnover_limit(sell_count=15, current_count=20)

    def test_no_current_holdings_ok(self, executor) -> None:
        """현재 보유 0개 → 검증 스킵"""
        executor._check_turnover_limit(sell_count=0, current_count=0)

    def test_full_turnover_blocked(self, executor, mock_api) -> None:
        """100% 교체 시 리밸런싱 차단"""
        balance = {
            "holdings": [
                {"ticker": f"T{i:04d}", "qty": 100} for i in range(10)
            ],
            "cash": 1000000,
            "total_eval_amount": 50000000,
            "total_profit": 0,
        }
        mock_api.get_balance.return_value = balance
        with pytest.raises(TurnoverLimitExceeded):
            executor.execute_rebalancing(
                current_holdings=[f"T{i:04d}" for i in range(10)],
                target_portfolio=[f"N{i:04d}" for i in range(10)],
            )


class TestExecuteRebalancing:
    """리밸런싱 실행 테스트"""

    @patch("time.sleep")
    def test_sell_before_buy(self, mock_sleep, executor, mock_api) -> None:
        """매도가 매수보다 먼저 실행"""
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크 (avg_price 없음 → 스톱 미발동)
            _no_stop_balance(
                holdings=[
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "035720", "qty": 50},
                ],
                cash=1000000,
                total_eval_amount=8000000,
            ),
            # ① 매도 전 잔고 확인 (2종목 보유, 1개 매도 = 50%)
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "035720", "qty": 50},
                ],
                "cash": 1000000,
                "total_eval_amount": 8000000,
                "total_profit": 0,
            },
            # ② 매수 전 잔고 재확인
            {
                "holdings": [{"ticker": "035720", "qty": 50}],
                "cash": 8000000,
                "total_eval_amount": 8000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_unfilled_orders.return_value = []
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "035720"],
            target_portfolio=["035720", "000660"],
        )

        assert "005930" in sell_done
        assert "000660" in buy_done

        # 매도가 먼저 호출됨
        calls = mock_api.method_calls
        sell_idx = next(i for i, c in enumerate(calls) if c[0] == "sell_stock")
        buy_idx = next(i for i, c in enumerate(calls) if c[0] == "buy_stock")
        assert sell_idx < buy_idx

    def test_buy_uses_99_percent_cash(self, executor, mock_api) -> None:
        """매수 시 예수금의 99%만 사용"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
        )

        buy_call = mock_api.buy_stock.call_args
        qty = buy_call.kwargs.get("qty") or buy_call[1].get("qty")
        expected_max = int(10000000 / 50000)  # 200
        assert qty < expected_max
        assert qty > 0

    def test_no_buy_when_zero_price(self, executor, mock_api) -> None:
        """현재가 0이면 매수 스킵"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 0}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
        )
        assert buy_done == []
        mock_api.buy_stock.assert_not_called()

    @patch("time.sleep")
    def test_empty_target_liquidates_all(
        self, mock_sleep, executor, mock_api
    ) -> None:
        """목표 포트폴리오 비어있으면 전량 매도 (턴오버 50% 이내인 경우)"""
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크
            _no_stop_balance(
                holdings=[{"ticker": "005930", "qty": 100}],
                cash=1000000,
                total_eval_amount=10000000,
            ),
            # ① 매도 전
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                ],
                "cash": 1000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
            # ② 매수 전 잔고 재확인
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_unfilled_orders.return_value = []

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930"],
            target_portfolio=[],
        )
        assert "005930" in sell_done
        assert buy_done == []

    @patch("time.sleep")
    def test_sell_failure_continues(self, mock_sleep, executor, mock_api) -> None:
        """매도 실패해도 나머지 계속 실행 (턴오버 50% 이내)"""
        holdings_4 = [
            {"ticker": "005930", "qty": 100},
            {"ticker": "000660", "qty": 50},
            {"ticker": "035720", "qty": 80},
            {"ticker": "051910", "qty": 60},
        ]
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크
            _no_stop_balance(
                holdings=holdings_4,
                cash=1000000,
                total_eval_amount=20000000,
            ),
            # ① 매도 전 (4종목 보유, 2개 매도 = 50%)
            {
                "holdings": holdings_4,
                "cash": 1000000,
                "total_eval_amount": 20000000,
                "total_profit": 0,
            },
            # ② 매수 전 잔고 재확인
            {
                "holdings": [
                    {"ticker": "035720", "qty": 80},
                    {"ticker": "051910", "qty": 60},
                ],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        # sorted 순서: 000660 먼저, 005930 나중
        mock_api.sell_stock.side_effect = [
            {"return_code": -1, "return_msg": "실패"},  # 000660 실패
            {"return_code": 0, "ord_no": "S002"},  # 005930 성공
        ]
        mock_api.get_unfilled_orders.return_value = []
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "000660", "035720", "051910"],
            target_portfolio=["035720", "051910", "066570", "068270"],
        )
        assert "005930" in sell_done
        assert "000660" not in sell_done


class TestPositionSizeLimit:
    """단일 종목 최대 비중 제한 테스트"""

    def test_position_limited_to_max_pct(self, executor, mock_api) -> None:
        """총평가 1억 + max_position_pct=10% → 종목당 최대 1000만원"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 100000000,  # 1억
                "total_eval_amount": 100000000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],  # 1종목 → 현금 100% 쓸 수 있지만
        )

        buy_call = mock_api.buy_stock.call_args
        qty = buy_call.kwargs.get("qty") or buy_call[1].get("qty")
        # max_position = 1억 * 10% = 1000만 / 50000 ≈ 199주
        assert qty <= 200
        # 전체 현금으로 사면 약 1980주 → 확실히 제한됨
        assert qty < 1000


class TestSettleWithOrderNos:
    """주문번호 기반 체결 확인 테스트"""

    @patch("time.sleep")
    def test_settle_by_order_number(self, mock_sleep, executor, mock_api) -> None:
        """get_unfilled_orders() 기반 체결 확인"""
        mock_api.get_unfilled_orders.side_effect = [
            [{"ord_no": "S001"}],  # 첫 폴링: 미체결
            [],  # 두 번째 폴링: 체결 완료
        ]

        executor._wait_for_sells_to_settle(
            sold_tickers=["005930"],
            order_nos=["S001"],
        )

        assert mock_api.get_unfilled_orders.call_count == 2

    @patch("time.sleep")
    def test_settle_fallback_to_balance(self, mock_sleep, executor, mock_api) -> None:
        """주문번호 없으면 잔고 폴백"""
        mock_api.get_balance.return_value = {
            "holdings": [],
            "cash": 10000000,
            "total_eval_amount": 10000000,
            "total_profit": 0,
        }

        executor._wait_for_sells_to_settle(
            sold_tickers=["005930"],
            order_nos=[],
        )

        mock_api.get_balance.assert_called()


class TestPaperTradingGuard:
    """모의투자 안전장치 테스트"""

    def test_paper_trading_uses_krx_exchange(self, executor, mock_api) -> None:
        """모의투자 시 exchange=KRX 강제"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0}

        executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
        )

        buy_call = mock_api.buy_stock.call_args
        exchange = buy_call.kwargs.get("exchange") or buy_call[1].get("exchange")
        assert exchange == "KRX"

    def test_real_trading_warning(self) -> None:
        """실전 전환 시 WARNING 로그"""
        with patch("trading.order.settings") as mock_settings:
            mock_settings.is_paper_trading = False
            mock_settings.trading.commission_rate = 0.00015
            mock_settings.trading.max_position_pct = 0.10
            mock_settings.trading.max_turnover_pct = 0.50
            mock_settings.trading.max_drawdown_pct = 0.30
            mock_api = MagicMock()
            mock_api.is_paper = False
            with patch("trading.order.KiwoomRestClient", return_value=mock_api):
                with patch("trading.order.logger") as mock_logger:
                    OrderExecutor()
                    mock_logger.warning.assert_called()


class TestCapitalAdaptiveAllocation:
    """자본 적응형 배분 테스트 (소액 자본에서 비싼 종목 자동 제외)"""

    def test_expensive_stock_excluded_and_redistributed(
        self, executor, mock_api
    ) -> None:
        """비싼 종목 제외 후 남은 종목에 재분배"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 1_000_000,  # 100만원
                "total_eval_amount": 1_000_000,
                "total_profit": 0,
            },
        ]
        # 3종목: A=5만, B=60만(비쌈), C=8만
        # 종목당 33만원 → B는 1주도 못 삼 → 제외
        # → 2종목, 종목당 49.5만원 (99%)
        mock_api.get_current_price.side_effect = [
            {"current_price": 50000},   # A
            {"current_price": 600000},  # B (비쌈)
            {"current_price": 80000},   # C
        ]
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["A", "B", "C"],
        )

        # B는 제외되고 A, C만 매수
        assert "A" in buy_done
        assert "C" in buy_done
        assert "B" not in buy_done
        # buy_stock은 2번만 호출 (B 제외)
        assert mock_api.buy_stock.call_count == 2

    def test_all_stocks_too_expensive(self, executor, mock_api) -> None:
        """모든 종목이 너무 비싸면 매수 0건"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 100_000,  # 10만원
                "total_eval_amount": 100_000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 500000}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["A", "B"],
        )
        assert buy_done == []
        mock_api.buy_stock.assert_not_called()


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_holding_not_found_in_balance(self, executor, mock_api) -> None:
        """잔고에 없는 종목 매도 시도 → 스킵"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(cash=5000000, total_eval_amount=5000000),  # ⓪ 트레일링 스톱 체크
            {
                "holdings": [],
                "cash": 5000000,
                "total_eval_amount": 5000000,
                "total_profit": 0,
            },
            {
                "holdings": [],
                "cash": 5000000,
                "total_eval_amount": 5000000,
                "total_profit": 0,
            },
        ]

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930"],
            target_portfolio=[],
        )
        assert sell_done == []
        mock_api.sell_stock.assert_not_called()

    def test_insufficient_budget_per_stock(self, executor, mock_api) -> None:
        """예산 부족 시 매수 스킵"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {"holdings": [], "cash": 100, "total_eval_amount": 100, "total_profit": 0},
        ]
        mock_api.get_current_price.return_value = {"current_price": 500000}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
        )
        assert buy_done == []


class TestDrawdownCircuitBreaker:
    """MDD 서킷 브레이커 테스트"""

    def test_drawdown_within_limit(self, executor) -> None:
        """고점 대비 20% 하락 → 30% 한도 내 → 통과 (False)"""
        executor._peak_value = 10_000_000
        assert executor._check_drawdown(8_000_000) is False  # -20% → 정상

    def test_drawdown_exceeds_limit(self, executor) -> None:
        """고점 대비 35% 하락 → 30% 한도 초과 → 발동 (True)"""
        executor._peak_value = 10_000_000
        assert executor._check_drawdown(6_500_000) is True  # -35%

    def test_drawdown_updates_peak(self, executor) -> None:
        """신고점 갱신"""
        executor._peak_value = 10_000_000
        executor._check_drawdown(12_000_000)
        assert executor._peak_value == 12_000_000

    @patch("time.sleep")
    def test_drawdown_triggers_emergency_liquidation(
        self, mock_sleep, executor, mock_api
    ) -> None:
        """리밸런싱 중 MDD 초과 → 전량 매도 후 빈 buy 반환"""
        executor._peak_value = 50_000_000  # 고점 5000만
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크
            _no_stop_balance(
                holdings=[
                    {"ticker": "005930", "qty": 100, "current_price": 150000},
                ],
                cash=1000000,
                total_eval_amount=30_000_000,
            ),
            # ① 매도 전 잔고 (MDD 체크)
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100, "current_price": 150000},
                ],
                "cash": 1000000,
                "total_eval_amount": 30_000_000,  # -40% 하락
                "total_profit": -20_000_000,
            },
            # ② execute_emergency_liquidation 내부 get_balance
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100, "current_price": 150000},
                ],
                "cash": 1000000,
                "total_eval_amount": 30_000_000,
                "total_profit": -20_000_000,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_unfilled_orders.return_value = []

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930"],
            target_portfolio=["005930", "000660"],
        )

        # 전량 매도됨
        assert "005930" in sell_done
        # 매수 없음 (서킷브레이커 발동)
        assert buy_done == []
        # CB 상태 활성화 확인
        assert executor._circuit_breaker_active is True


class TestTrailingStop:
    """트레일링 스톱 테스트"""

    def test_no_stop_when_disabled(self, executor) -> None:
        """trailing_stop_pct=0이면 발동 안 함"""
        executor.cfg.trailing_stop_pct = 0
        balance = {
            "holdings": [
                {"ticker": "005930", "qty": 100, "avg_price": 100000, "current_price": 50000},
            ],
        }
        result = executor._check_trailing_stops(balance)
        assert result == []

    def test_stop_triggered(self, executor) -> None:
        """매수가 대비 -25% 하락 → 20% 스톱 발동"""
        balance = {
            "holdings": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "qty": 100,
                    "avg_price": 100000,
                    "current_price": 75000,  # -25%
                },
            ],
        }
        result = executor._check_trailing_stops(balance)
        assert "005930" in result

    def test_no_stop_within_threshold(self, executor) -> None:
        """매수가 대비 -10% 하락 → 20% 한도 내 → 미발동"""
        balance = {
            "holdings": [
                {
                    "ticker": "005930",
                    "qty": 100,
                    "avg_price": 100000,
                    "current_price": 90000,  # -10%
                },
            ],
        }
        result = executor._check_trailing_stops(balance)
        assert result == []

    def test_stop_skips_zero_avg_price(self, executor) -> None:
        """avg_price가 0이면 스킵"""
        balance = {
            "holdings": [
                {"ticker": "005930", "qty": 100, "avg_price": 0, "current_price": 50000},
            ],
        }
        result = executor._check_trailing_stops(balance)
        assert result == []

    @patch("time.sleep")
    def test_stop_ticker_removed_from_target(self, mock_sleep, executor, mock_api) -> None:
        """스톱 발동 종목은 target에서 제거되어 재매수 안 됨"""
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크 — 005930이 -25% 하락
            {
                "holdings": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "qty": 100,
                        "avg_price": 100000,
                        "current_price": 75000,  # -25% → 스톱 발동
                    },
                    {
                        "ticker": "000660",
                        "name": "SK하이닉스",
                        "qty": 50,
                        "avg_price": 150000,
                        "current_price": 160000,  # +6.7% → 정상
                    },
                ],
                "cash": 1000000,
                "total_eval_amount": 16500000,
                "total_profit": 0,
            },
            # ① 매도 전 잔고 (같은 상태)
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "000660", "qty": 50},
                ],
                "cash": 1000000,
                "total_eval_amount": 16500000,
                "total_profit": 0,
            },
            # ② 매수 전 잔고
            {
                "holdings": [{"ticker": "000660", "qty": 50}],
                "cash": 9000000,
                "total_eval_amount": 17000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_unfilled_orders.return_value = []
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "000660"],
            # target에 005930이 있지만 스톱 발동으로 제거되어야 함
            target_portfolio=["005930", "000660", "035720"],
        )

        # 005930은 스톱 발동으로 매도됨
        assert "005930" in sell_done
        # 005930은 재매수 대상이 아님 (target에서 제거됨)
        assert "005930" not in buy_done
        # 035720은 신규 매수됨
        assert "035720" in buy_done


class TestCircuitBreakerReentry:
    """서킷브레이커 재진입 테스트"""

    def test_reentry_when_dd_recovers(self, executor) -> None:
        """DD가 발동 기준의 50% 이내로 회복 → 재진입 허용"""
        executor._peak_value = 10_000_000
        executor._circuit_breaker_active = True
        # max_dd=0.30, reentry threshold = -0.30 * 0.5 = -0.15
        # 현재 8_600_000 → DD = -14% > -15% → 재진입
        assert executor.check_circuit_breaker_reentry(8_600_000) is True
        assert executor._circuit_breaker_active is False

    def test_no_reentry_when_still_deep(self, executor) -> None:
        """DD가 여전히 깊으면 재진입 불허"""
        executor._peak_value = 10_000_000
        executor._circuit_breaker_active = True
        # 현재 7_000_000 → DD = -30% < -15% → 유지
        assert executor.check_circuit_breaker_reentry(7_000_000) is False
        assert executor._circuit_breaker_active is True

    def test_no_cb_always_allows(self, executor) -> None:
        """CB 비활성이면 항상 True"""
        executor._circuit_breaker_active = False
        assert executor.check_circuit_breaker_reentry(5_000_000) is True


class TestInvestRatio:
    """투자 비중 (시장 레짐/변동성 타겟팅) 반영 테스트"""

    def test_invest_ratio_scales_budget(self, executor, mock_api) -> None:
        """invest_ratio=0.5 → 매수 예산 50%로 축소"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),  # ⓪ 트레일링 스톱 체크
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 10_000_000,
                "total_eval_amount": 10_000_000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
            invest_ratio=0.5,
        )

        buy_call = mock_api.buy_stock.call_args
        qty = buy_call.kwargs.get("qty") or buy_call[1].get("qty")
        # 10M * 0.99 * 0.5 = 4,950,000 / 50,000 ≈ 98주
        # invest_ratio 없으면 약 197주
        assert qty < 120
        assert qty > 0

    def test_invest_ratio_1_no_scaling(self, executor, mock_api) -> None:
        """invest_ratio=1.0 → 스케일링 없음 (max_position_pct로 제한됨)"""
        mock_api.get_balance.side_effect = [
            _no_stop_balance(),
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {
                "holdings": [],
                "cash": 10_000_000,
                "total_eval_amount": 10_000_000,
                "total_profit": 0,
            },
        ]
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
            invest_ratio=1.0,
        )

        buy_call = mock_api.buy_stock.call_args
        qty = buy_call.kwargs.get("qty") or buy_call[1].get("qty")
        # max_position_pct=10% → 10M*10%=1M / 50,000 ≈ 19주
        assert qty > 0
        assert qty <= 20
