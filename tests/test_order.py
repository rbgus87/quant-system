# tests/test_order.py
import pytest
from unittest.mock import patch, MagicMock

from trading.order import OrderExecutor


@pytest.fixture
def mock_api():
    """KiwoomRestClient mock"""
    api = MagicMock()
    api.is_paper = True
    return api


@pytest.fixture
def executor(mock_api):
    """OrderExecutor with mocked API"""
    with patch("trading.order.settings") as mock_settings:
        mock_settings.is_paper_trading = True
        mock_settings.trading.commission_rate = 0.00015
        with patch("trading.order.KiwoomRestClient", return_value=mock_api):
            ex = OrderExecutor()
    return ex


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


class TestExecuteRebalancing:
    """리밸런싱 실행 테스트"""

    @patch("time.sleep")
    def test_sell_before_buy(self, mock_sleep, executor, mock_api) -> None:
        """매도가 매수보다 먼저 실행"""
        mock_api.get_balance.side_effect = [
            # ① 매도 전 잔고 확인
            {
                "holdings": [{"ticker": "005930", "qty": 100}],
                "cash": 1000000,
                "total_eval_amount": 8000000,
                "total_profit": 0,
            },
            # ② 매도 체결 대기 (005930 체결 완료)
            {
                "holdings": [],
                "cash": 8000000,
                "total_eval_amount": 8000000,
                "total_profit": 0,
            },
            # ③ 매수 전 예수금 재확인
            {
                "holdings": [],
                "cash": 8000000,
                "total_eval_amount": 8000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930"],
            target_portfolio=["000660"],
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
        """목표 포트폴리오 비어있으면 전량 매도"""
        mock_api.get_balance.side_effect = [
            # ① 매도 전
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "000660", "qty": 50},
                ],
                "cash": 1000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
            # ② 매도 체결 대기 (전부 체결)
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
            # ③ 예수금 재확인
            {
                "holdings": [],
                "cash": 10000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "000660"],
            target_portfolio=[],
        )
        assert set(sell_done) == {"005930", "000660"}
        assert buy_done == []

    @patch("time.sleep")
    def test_sell_failure_continues(self, mock_sleep, executor, mock_api) -> None:
        """매도 실패해도 나머지 계속 실행"""
        mock_api.get_balance.side_effect = [
            # ① 매도 전
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "000660", "qty": 50},
                ],
                "cash": 1000000,
                "total_eval_amount": 10000000,
                "total_profit": 0,
            },
            # ② 매도 체결 대기 (005930만 체결)
            {
                "holdings": [{"ticker": "000660", "qty": 50}],
                "cash": 5000000,
                "total_eval_amount": 5000000,
                "total_profit": 0,
            },
            # ③ 타임아웃 후 예수금 재확인
            {
                "holdings": [{"ticker": "000660", "qty": 50}],
                "cash": 5000000,
                "total_eval_amount": 5000000,
                "total_profit": 0,
            },
            # ④ 추가 폴링 (타임아웃까지)
            {
                "holdings": [{"ticker": "000660", "qty": 50}],
                "cash": 5000000,
                "total_eval_amount": 5000000,
                "total_profit": 0,
            },
        ] * 5  # 충분한 poll 횟수
        # sorted 순서: 000660 먼저, 005930 나중
        mock_api.sell_stock.side_effect = [
            {"return_code": -1, "return_msg": "실패"},  # 000660 실패
            {"return_code": 0, "ord_no": "S002"},  # 005930 성공
        ]

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "000660"],
            target_portfolio=[],
        )
        assert "005930" in sell_done
        assert "000660" not in sell_done


class TestPaperTradingGuard:
    """모의투자 안전장치 테스트"""

    def test_paper_trading_uses_krx_exchange(self, executor, mock_api) -> None:
        """모의투자 시 exchange=KRX 강제"""
        mock_api.get_balance.side_effect = [
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
            mock_api = MagicMock()
            mock_api.is_paper = False
            with patch("trading.order.KiwoomRestClient", return_value=mock_api):
                with patch("trading.order.logger") as mock_logger:
                    OrderExecutor()
                    mock_logger.warning.assert_called()


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_holding_not_found_in_balance(self, executor, mock_api) -> None:
        """잔고에 없는 종목 매도 시도 → 스킵"""
        mock_api.get_balance.side_effect = [
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
            {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0},
            {"holdings": [], "cash": 100, "total_eval_amount": 100, "total_profit": 0},
        ]
        mock_api.get_current_price.return_value = {"current_price": 500000}

        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=[],
            target_portfolio=["005930"],
        )
        assert buy_done == []
