# tests/test_screener.py
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from strategy.screener import MultiFactorScreener


class TestReportingLag:
    """Reporting Lag 처리 테스트"""

    def test_jan_uses_two_years_ago(self) -> None:
        """1~3월 리밸런싱 → 전전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240229")
        assert result.startswith("2022")
        assert result >= "20221226"  # 12월 마지막 거래일은 26~30일 사이

    def test_apr_uses_prev_year(self) -> None:
        """4월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240430")
        assert result.startswith("2023")

    def test_dec_uses_prev_year(self) -> None:
        """12월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20241231")
        assert result.startswith("2023")

    def test_jun_uses_prev_year(self) -> None:
        """6월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240628")
        assert result.startswith("2023")


class TestMultiFactorScreener:
    """MultiFactorScreener 통합 테스트 (mock 기반)"""

    def setup_method(self) -> None:
        """테스트 간 팩터 캐시 오염 방지"""
        MultiFactorScreener._factor_cache.clear()

    def _make_fundamentals(self, n: int = 50) -> pd.DataFrame:
        """테스트용 기본 지표 DataFrame 생성"""
        np.random.seed(42)
        tickers = [f"T{i:04d}" for i in range(n)]
        return pd.DataFrame(
            {
                "BPS": np.random.uniform(10000, 100000, n),
                "PER": np.random.uniform(3, 30, n),
                "PBR": np.random.uniform(0.3, 5.0, n),
                "EPS": np.random.uniform(1000, 20000, n),
                "DIV": np.random.uniform(0, 5, n),
            },
            index=tickers,
        )

    def _make_market_cap(self, tickers: list[str]) -> pd.DataFrame:
        """테스트용 시가총액 DataFrame 생성"""
        np.random.seed(42)
        n = len(tickers)
        return pd.DataFrame(
            {
                "market_cap": np.random.uniform(1e10, 1e14, n),
                "shares": np.random.randint(1000000, 100000000, n),
            },
            index=tickers,
        )

    def _make_returns(self, tickers: list[str]) -> pd.Series:
        """테스트용 수익률 Series 생성"""
        np.random.seed(42)
        return pd.Series(
            np.random.uniform(-0.3, 0.5, len(tickers)),
            index=tickers,
            name="return_12m",
        )

    def _make_avg_trading_value(self, tickers: list[str]) -> pd.Series:
        """테스트용 평균 거래대금 Series (모두 유동성 통과)"""
        np.random.seed(42)
        return pd.Series(
            np.random.uniform(1e8, 1e10, len(tickers)),
            index=tickers,
        )

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_all_market(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        """ALL 시장 — KOSPI+KOSDAQ 각각 호출 확인"""
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        result = screener.screen("20240102", market="ALL", n_stocks=10)

        # ALL 모드에서 KOSPI, KOSDAQ 2번 호출
        assert mock_collector.get_fundamentals_all.call_count == 2
        assert mock_collector.get_market_cap.call_count == 2
        assert len(result) <= 10

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_basic(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        result = screener.screen("20240102", n_stocks=10)

        assert len(result) <= 10
        assert "composite_score" in result.columns
        assert "weight" in result.columns
        if len(result) > 0:
            assert abs(result["weight"].sum() - 1.0) < 0.01

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_empty_fundamentals(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = pd.DataFrame()

        screener = MultiFactorScreener()
        result = screener.screen("20240102")

        assert result.empty

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_with_finance_filter(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        finance = tickers[:5]
        result = screener.screen("20240102", finance_tickers=finance, n_stocks=30)

        # 금융주로 지정된 종목은 결과에 포함되지 않아야 함
        for t in finance:
            assert t not in result.index

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_exception_handling(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        """예외 발생 시 빈 DataFrame 반환"""
        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.side_effect = Exception("네트워크 오류")

        screener = MultiFactorScreener()
        result = screener.screen("20240102")

        assert result.empty

class TestSectorDiversification:
    """_select_with_sector_diversification — S4-B 섹터 분산 제약"""

    def setup_method(self) -> None:
        MultiFactorScreener._factor_cache.clear()

    @staticmethod
    def _make_screener_with_sectors(sector_map: dict[str, str]) -> MultiFactorScreener:
        screener = MultiFactorScreener.__new__(MultiFactorScreener)
        screener.collector = MagicMock()
        screener.composite = MagicMock()

        def _fake_select_top(df, n=20):
            return df.head(n).copy().assign(weight=lambda d: 1.0 / max(len(d), 1))
        screener.composite.select_top = _fake_select_top

        sector_df = pd.DataFrame(
            {"sector_name": list(sector_map.values())},
            index=list(sector_map.keys()),
        )
        screener.collector.storage.load_stock_sectors = (
            lambda date, market="KOSPI": sector_df
        )
        return screener

    def _composite_df(self, tickers: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {"composite_score": [100.0 - i for i in range(len(tickers))]},
            index=tickers,
        )

    def test_sector_overconcentration_replaced(self, monkeypatch) -> None:
        from config.settings import settings as cfg
        monkeypatch.setattr(cfg.universe, "sector_diversification_enabled", True)
        monkeypatch.setattr(cfg.universe, "max_sector_count", 4)
        monkeypatch.setattr(cfg.universe, "sector_exempt_names", ["기타"])

        tickers = [f"T{i}" for i in range(8)]
        sector_map = {
            "T0": "화학", "T1": "화학", "T2": "화학",
            "T3": "화학", "T4": "화학",
            "T5": "전자IT", "T6": "유통", "T7": "건설",
        }
        screener = self._make_screener_with_sectors(sector_map)
        composite = self._composite_df(tickers)

        result = screener._select_with_sector_diversification(
            composite, "20240630", n_stocks=5,
        )
        assert len(result) == 5
        result_sectors = [sector_map[t] for t in result.index]
        assert result_sectors.count("화학") == 4
        assert "T4" not in result.index
        assert "T5" in result.index

    def test_no_overconcentration_unchanged(self, monkeypatch) -> None:
        from config.settings import settings as cfg
        monkeypatch.setattr(cfg.universe, "sector_diversification_enabled", True)
        monkeypatch.setattr(cfg.universe, "max_sector_count", 4)
        monkeypatch.setattr(cfg.universe, "sector_exempt_names", ["기타"])

        tickers = [f"T{i}" for i in range(5)]
        sector_map = {
            "T0": "화학", "T1": "전자IT",
            "T2": "유통", "T3": "건설", "T4": "자동차",
        }
        screener = self._make_screener_with_sectors(sector_map)
        composite = self._composite_df(tickers)

        result = screener._select_with_sector_diversification(
            composite, "20240630", n_stocks=5,
        )
        assert len(result) == 5
        assert list(result.index) == tickers

    def test_etc_sector_exempt(self, monkeypatch) -> None:
        from config.settings import settings as cfg
        monkeypatch.setattr(cfg.universe, "sector_diversification_enabled", True)
        monkeypatch.setattr(cfg.universe, "max_sector_count", 4)
        monkeypatch.setattr(cfg.universe, "sector_exempt_names", ["기타"])

        tickers = [f"T{i}" for i in range(6)]
        sector_map = {t: "기타" for t in tickers}
        screener = self._make_screener_with_sectors(sector_map)
        composite = self._composite_df(tickers)

        result = screener._select_with_sector_diversification(
            composite, "20240630", n_stocks=6,
        )
        assert len(result) == 6

    def test_insufficient_candidates(self, monkeypatch) -> None:
        from config.settings import settings as cfg
        monkeypatch.setattr(cfg.universe, "sector_diversification_enabled", True)
        monkeypatch.setattr(cfg.universe, "max_sector_count", 2)
        monkeypatch.setattr(cfg.universe, "sector_exempt_names", ["기타"])

        tickers = [f"T{i}" for i in range(5)]
        sector_map = {t: "화학" for t in tickers}
        screener = self._make_screener_with_sectors(sector_map)
        composite = self._composite_df(tickers)

        result = screener._select_with_sector_diversification(
            composite, "20240630", n_stocks=5,
        )
        assert len(result) == 2
        assert list(result.index) == ["T0", "T1"]


class TestSanityReport:
    """generate_sanity_report — E3 선정 종목 펀더멘털 요약"""

    def setup_method(self) -> None:
        MultiFactorScreener._factor_cache.clear()

    @staticmethod
    def _make_screener(
        fundamentals: pd.DataFrame, sector_map: dict[str, str],
    ) -> MultiFactorScreener:
        screener = MultiFactorScreener.__new__(MultiFactorScreener)
        screener.collector = MagicMock()
        # storage.load_fundamentals: KOSPI/KOSDAQ 호출 모두 같은 df 반환
        screener.collector.storage.load_fundamentals = (
            lambda dt, market="KOSPI": fundamentals if market == "KOSPI" else pd.DataFrame()
        )
        sector_df = pd.DataFrame(
            {"sector_name": list(sector_map.values())},
            index=list(sector_map.keys()),
        )
        screener.collector.storage.load_stock_sectors = (
            lambda date, market="KOSPI": sector_df
        )
        screener.collector.get_ticker_name = lambda t: f"{t}_NAME"
        return screener

    def test_returns_markdown_with_summary(self) -> None:
        tickers = ["T0", "T1", "T2"]
        portfolio_df = pd.DataFrame(
            {
                "composite_score": [90.0, 80.0, 70.0],
                "weight": [1 / 3, 1 / 3, 1 / 3],
            },
            index=tickers,
        )
        fundamentals = pd.DataFrame(
            {
                "PBR": [0.5, 1.2, 2.0],
                "PER": [5.0, 10.0, 15.0],
                "OPERATING_INCOME": [1e10, 2e10, 3e10],
                "DEBT_RATIO": [80.0, 120.0, 150.0],
            },
            index=tickers,
        )
        sector_map = {"T0": "전자IT", "T1": "화학", "T2": "유통"}
        screener = self._make_screener(fundamentals, sector_map)

        report = screener.generate_sanity_report(portfolio_df, "20240630")

        assert "Sanity Report" in report
        assert "20240630" in report
        assert "포트폴리오 요약" in report
        assert "종목 수 | 3" in report
        assert "T0" in report and "T1" in report and "T2" in report
        # 정상 종목 → 자동 플래그 해당 없음
        assert "자동 플래그 해당 종목 없음" in report

    def test_flags_high_debt_and_negative_op_income(self) -> None:
        tickers = ["T0", "T1", "T2"]
        portfolio_df = pd.DataFrame(
            {"composite_score": [90.0, 80.0, 70.0]},
            index=tickers,
        )
        fundamentals = pd.DataFrame(
            {
                "PBR": [0.5, 1.0, 5.0],            # T2 PBR>3
                "PER": [5.0, 10.0, 15.0],
                "OPERATING_INCOME": [1e10, -5e9, 3e10],  # T1 음수
                "DEBT_RATIO": [50.0, 100.0, 400.0],      # T2 부채 400% > 300%
            },
            index=tickers,
        )
        sector_map = {"T0": "전자IT", "T1": "화학", "T2": "기타"}  # T2 sector=기타
        screener = self._make_screener(fundamentals, sector_map)

        report = screener.generate_sanity_report(portfolio_df, "20240630")

        # T1: op_income 음수 플래그
        assert "T1" in report
        assert "op_income 결측/음수" in report
        # T2: PBR>3 + 부채>300% + sector=기타
        assert "PBR 5.0" in report or "PBR 5" in report
        assert "debt 400%" in report
        assert "sector=기타" in report

    def test_empty_portfolio_returns_placeholder(self) -> None:
        screener = self._make_screener(pd.DataFrame(), {})
        report = screener.generate_sanity_report(pd.DataFrame(), "20240630")
        assert "선정 종목 없음" in report
