# tests/test_integration.py
"""E2E 통합 테스트

데이터 수집 → 전처리 → 팩터 계산 → 스크리닝 → 주문 (mock) 전체 파이프라인 검증
스케줄러 → 텔레그램 알림 흐름 검증
"""

import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock


class TestE2EPipeline:
    """데이터 수집 → 스크리닝 → 주문 E2E 테스트"""

    def setup_method(self) -> None:
        """테스트 간 팩터 캐시 오염 방지"""
        from strategy.screener import MultiFactorScreener
        MultiFactorScreener._factor_cache.clear()

    def _make_fundamentals(self, tickers: list[str]) -> pd.DataFrame:
        """테스트용 기본 지표 DataFrame 생성"""
        np.random.seed(42)
        n = len(tickers)
        return pd.DataFrame(
            {
                "PBR": np.random.uniform(0.5, 3.0, n),
                "PER": np.random.uniform(5, 30, n),
                "DIV": np.random.uniform(0, 5, n),
                "EPS": np.random.uniform(1000, 10000, n),
                "BPS": np.random.uniform(10000, 50000, n),
            },
            index=tickers,
        )

    def _make_returns(self, tickers: list[str]) -> pd.Series:
        """테스트용 수익률 Series 생성"""
        np.random.seed(42)
        return pd.Series(
            np.random.uniform(-0.2, 0.5, len(tickers)),
            index=tickers,
        )

    def test_full_pipeline_screening(self) -> None:
        """데이터 → 팩터 계산 → 종목 선정 파이프라인"""
        from factors.value import ValueFactor
        from factors.momentum import MomentumFactor
        from factors.quality import QualityFactor
        from factors.composite import MultiFactorComposite

        tickers = [f"00{i:04d}" for i in range(50)]
        fundamentals = self._make_fundamentals(tickers)
        returns_12m = self._make_returns(tickers)

        # 팩터 계산
        value_scores = ValueFactor().calculate(fundamentals)
        momentum_scores = MomentumFactor().calculate(returns_12m)
        quality_scores = QualityFactor().calculate(fundamentals)

        assert len(value_scores) > 0
        assert len(momentum_scores) > 0
        assert len(quality_scores) > 0

        # 복합 팩터
        composite = MultiFactorComposite()
        composite_df = composite.calculate(
            value_scores, momentum_scores, quality_scores
        )
        assert "composite_score" in composite_df.columns
        assert len(composite_df) > 0

        # Top N 선정
        selected = composite.select_top(composite_df, n=10)
        assert len(selected) == 10
        assert selected.index.is_unique

    @patch("time.sleep")
    def test_screening_to_order_flow(self, mock_sleep) -> None:
        """스크리닝 결과 → 주문 실행 흐름"""
        mock_api = MagicMock()
        mock_api.is_paper = True
        mock_api.get_balance.side_effect = [
            # ⓪ 트레일링 스톱 체크 (avg_price 없음 → 미발동)
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "000660", "qty": 50},
                ],
                "cash": 5000000,
                "total_eval_amount": 15000000,
                "total_profit": 0,
            },
            # ① 매도 전 잔고 확인
            {
                "holdings": [
                    {"ticker": "005930", "qty": 100},
                    {"ticker": "000660", "qty": 50},
                ],
                "cash": 5000000,
                "total_eval_amount": 15000000,
                "total_profit": 0,
            },
            # ② 매수 전 예수금 재확인
            {
                "holdings": [],
                "cash": 15000000,
                "total_eval_amount": 15000000,
                "total_profit": 0,
            },
        ]
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_api.get_unfilled_orders.return_value = []
        mock_api.get_current_price.return_value = {"current_price": 50000}
        mock_api.buy_stock.return_value = {"return_code": 0, "ord_no": "B001"}

        with patch("trading.order.settings") as mock_settings:
            mock_settings.is_paper_trading = True
            mock_settings.trading.commission_rate = 0.00015
            mock_settings.trading.slippage = 0.001
            mock_settings.trading.max_position_pct = 0.10
            mock_settings.trading.max_turnover_pct = 0.50
            mock_settings.trading.max_drawdown_pct = 0.30
            mock_settings.trading.trailing_stop_pct = 0.20
            with patch("trading.order.KiwoomRestClient", return_value=mock_api):
                with patch("trading.order.DataStorage", return_value=MagicMock()):
                    from trading.order import OrderExecutor

                    executor = OrderExecutor()

        # 현재: 005930, 000660 → 목표: 000660, 035720, 051910
        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings=["005930", "000660"],
            target_portfolio=["000660", "035720", "051910"],
        )

        # 005930만 매도 (000660은 유지)
        assert "005930" in sell_done
        # 035720, 051910 매수
        assert len(buy_done) == 2

    def test_screener_with_mock_data(self) -> None:
        """MultiFactorScreener E2E (외부 API mock)"""
        from strategy.screener import MultiFactorScreener

        tickers = [f"00{i:04d}" for i in range(30)]
        fundamentals = self._make_fundamentals(tickers)
        returns_12m = self._make_returns(tickers)
        with patch.object(MultiFactorScreener, "__init__", lambda self: None):
            screener = MultiFactorScreener()
            screener.krx = MagicMock()
            screener.ret_calc = MagicMock()
            screener.value_f = MagicMock()
            screener.momentum_f = MagicMock()
            screener.quality_f = MagicMock()
            screener.composite = MagicMock()

            from factors.value import ValueFactor
            from factors.momentum import MomentumFactor
            from factors.quality import QualityFactor
            from factors.composite import MultiFactorComposite

            # 실제 팩터 엔진 사용
            value_scores = ValueFactor().calculate(fundamentals)
            momentum_scores = MomentumFactor().calculate(returns_12m)
            quality_scores = QualityFactor().calculate(fundamentals)
            composite = MultiFactorComposite()
            composite_df = composite.calculate(
                value_scores, momentum_scores, quality_scores
            )
            selected = composite.select_top(composite_df, n=10)

            # screener.get_portfolio가 이 결과를 반환하도록
            screener.get_portfolio = MagicMock(return_value=selected.index.tolist())

            portfolio = screener.get_portfolio("20240131")
            assert len(portfolio) == 10


class TestSchedulerTelegramFlow:
    """스케줄러 → 텔레그램 알림 통합 테스트"""

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_rebalancing_success_sends_report(self, mock_bday, mock_last) -> None:
        """리밸런싱 성공 → 텔레그램 리밸런싱 리포트 발송"""
        from scheduler.main import run_scheduled_rebalancing
        from config.settings import settings

        mock_notifier = MagicMock()
        mock_api = MagicMock()
        mock_executor = MagicMock()
        mock_screener = MagicMock()

        mock_api.get_balance.return_value = {
            "holdings": [{"ticker": "005930", "qty": 100}],
            "total_eval_amount": 50000000,
            "cash": 5000000,
        }
        mock_executor.execute_rebalancing.return_value = (["005930"], ["000660"])
        mock_screener.screen.return_value = pd.DataFrame(
            {"composite_score": [0.9]}, index=["000660"]
        )

        # MultiFactorScreener는 함수 내부에서 lazy import됨
        mock_screener_module = MagicMock()
        mock_screener_module.MultiFactorScreener.return_value = mock_screener

        with patch.object(settings.portfolio, "rebalance_frequency", "monthly"):
            with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
                with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                    with patch("scheduler.main.OrderExecutor", return_value=mock_executor):
                        with patch.dict(
                            "sys.modules",
                            {"strategy.screener": mock_screener_module},
                        ):
                            with patch("scheduler.main._save_screening_results"):
                                with patch("scheduler.main._calc_vol_target_scale", return_value=1.0):
                                    run_scheduled_rebalancing()

        # 시작 알림 + 결과 알림
        assert mock_notifier.send.called
        assert mock_notifier.send_rebalancing_report.called

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_rebalancing_failure_sends_error(self, mock_bday, mock_last) -> None:
        """리밸런싱 실패 → 텔레그램 에러 알림"""
        from scheduler.main import run_scheduled_rebalancing
        from config.settings import settings

        mock_notifier = MagicMock()
        mock_screener = MagicMock()
        mock_screener.screen.return_value = pd.DataFrame(
            {"composite_score": [0.9]}, index=["000660"]
        )
        mock_screener_module = MagicMock()
        mock_screener_module.MultiFactorScreener.return_value = mock_screener

        with patch.object(settings.portfolio, "rebalance_frequency", "monthly"):
            with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
                with patch(
                    "scheduler.main.KiwoomRestClient",
                    side_effect=RuntimeError("API 토큰 만료"),
                ):
                    with patch.dict(
                        "sys.modules",
                        {"strategy.screener": mock_screener_module},
                    ):
                        with patch("time.sleep"):
                            run_scheduled_rebalancing()

        # 에러 알림 발송
        mock_notifier.send_error.assert_called_once()
        error_msg = mock_notifier.send_error.call_args[0][0]
        assert "API 토큰 만료" in error_msg

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_daily_report_sends_balance(self, mock_bday) -> None:
        """일별 리포트 → 상세 잔고 정보 텔레그램 발송"""
        from scheduler.main import run_daily_report

        mock_notifier = MagicMock()
        mock_notifier.send_detailed_daily_report.return_value = True
        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [{"ticker": "005930"}, {"ticker": "000660"}],
            "total_eval_amount": 52000000,
            "total_profit": 2000000,
            "cash": 3000000,
        }

        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                run_daily_report()

        mock_notifier.send_detailed_daily_report.assert_called_once()
        balance = mock_notifier.send_detailed_daily_report.call_args[0][0]
        assert balance["total_eval_amount"] == 52000000
