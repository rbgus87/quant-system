# tests/test_factors.py
import pandas as pd
import numpy as np
import pytest

from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite

# ───────────────────────────────────────────────
# ValueFactor 테스트
# ───────────────────────────────────────────────


class TestValueFactor:
    def setup_method(self) -> None:
        self.factor = ValueFactor()

    def test_basic_calculation(self) -> None:
        df = pd.DataFrame(
            {
                "PBR": [0.5, 1.0, 2.0, 3.0, 5.0],
                "PER": [5.0, 10.0, 15.0, 20.0, 30.0],
                "DIV": [4.0, 3.0, 2.0, 1.0, 0.5],
            },
            index=["A", "B", "C", "D", "E"],
        )
        result = self.factor.calculate(df)

        assert result.name == "value_score"
        assert len(result) == 5
        # PBR 낮고, PER 낮고, DIV 높은 A가 최고 스코어
        assert result.index[0] == "A"
        # 모든 스코어가 0~100 범위
        assert result.min() >= 0
        assert result.max() <= 100

    def test_negative_pbr_included_via_other_factors(self) -> None:
        """PBR <= 0인 종목도 PER+DIV로 스코어 산출 (union 방식)"""
        df = pd.DataFrame(
            {
                "PBR": [-1.0, 0.0, 1.0, 2.0],
                "PER": [10.0, 10.0, 10.0, 10.0],
                "DIV": [1.0, 1.0, 1.0, 1.0],
            },
            index=["A", "B", "C", "D"],
        )
        result = self.factor.calculate(df)
        # PBR <= 0인 A, B도 PER+DIV 스코어로 포함됨
        assert "A" in result.index
        assert "B" in result.index
        # PBR이 유효한 C, D는 3개 지표 모두 반영
        assert "C" in result.index
        assert "D" in result.index

    def test_negative_per_included_via_other_factors(self) -> None:
        """적자 기업(PER <= 0)도 PBR+DIV로 스코어 산출 (union 방식)"""
        df = pd.DataFrame(
            {
                "PBR": [1.0, 1.0, 1.0],
                "PER": [-5.0, 0.0, 10.0],
                "DIV": [1.0, 1.0, 1.0],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        # PER이 무효해도 PBR+DIV로 스코어 산출
        assert "A" in result.index
        assert "B" in result.index
        assert "C" in result.index

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["PBR", "PER", "DIV"])
        result = self.factor.calculate(df)
        assert result.empty
        assert result.name == "value_score"

    def test_single_column(self) -> None:
        """PBR만 있어도 동작"""
        df = pd.DataFrame({"PBR": [1.0, 2.0, 3.0]}, index=["A", "B", "C"])
        result = self.factor.calculate(df)
        assert len(result) == 3

    def test_rank_score(self) -> None:
        series = pd.Series([10, 20, 30, 40, 50], index=list("ABCDE"))
        result = ValueFactor._rank_score(series)
        assert result["E"] == 100.0
        assert result["A"] == 20.0


# ───────────────────────────────────────────────
# MomentumFactor 테스트
# ───────────────────────────────────────────────


class TestMomentumFactor:
    def setup_method(self) -> None:
        self.factor = MomentumFactor()

    def test_basic_calculation(self) -> None:
        returns = pd.Series({"A": 0.30, "B": 0.10, "C": -0.05, "D": 0.50, "E": 0.20})
        result = self.factor.calculate(returns)

        assert result.name == "momentum_score"
        assert len(result) == 5
        # 수익률 가장 높은 D가 최고 스코어
        assert result.idxmax() == "D"

    def test_winsorize(self) -> None:
        """상하위 1% Winsorize 동작 확인"""
        np.random.seed(42)
        returns = pd.Series(
            np.concatenate([np.random.normal(0.1, 0.2, 98), [5.0, -5.0]]),
            index=[f"T{i:03d}" for i in range(100)],
        )
        result = self.factor.calculate(returns)
        assert len(result) == 100
        assert result.min() >= 0
        assert result.max() <= 100

    def test_composite_with_6m(self) -> None:
        returns_12m = pd.Series({"A": 0.30, "B": 0.10, "C": 0.20})
        returns_6m = pd.Series({"A": 0.15, "B": 0.25, "C": 0.05})

        result = self.factor.calculate(returns_12m, returns_6m=returns_6m)
        assert len(result) == 3
        assert result.name == "momentum_score"

    def test_empty_input(self) -> None:
        returns = pd.Series(dtype=float)
        result = self.factor.calculate(returns)
        assert result.empty

    def test_single_score_method(self) -> None:
        returns = pd.Series({"A": 0.10, "B": 0.20, "C": 0.30})
        result = MomentumFactor._single_score(returns)
        assert len(result) == 3
        assert result.max() <= 100


# ───────────────────────────────────────────────
# QualityFactor 테스트
# ───────────────────────────────────────────────


class TestQualityFactor:
    def setup_method(self) -> None:
        self.factor = QualityFactor()

    def test_basic_roe_calculation(self) -> None:
        df = pd.DataFrame(
            {
                "EPS": [5000, 3000, 1000, -500, 8000],
                "BPS": [50000, 30000, 20000, 10000, 40000],
            },
            index=["A", "B", "C", "D", "E"],
        )
        result = self.factor.calculate(df)

        assert result.name == "quality_score"
        # ROE: A=10%, B=10%, C=5%, D=-5%, E=20%
        # E가 최고, D가 최하 (음수 ROE도 포함되지만 낮은 순위)
        assert len(result) == 5

    def test_bps_zero_excluded(self) -> None:
        """자본잠식(BPS <= 0) 종목 제외"""
        df = pd.DataFrame(
            {
                "EPS": [5000, 3000, 1000],
                "BPS": [-1000, 0, 50000],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        assert "A" not in result.index
        assert "B" not in result.index
        assert "C" in result.index

    def test_roe_clipping(self) -> None:
        """ROE -50% ~ +100% 클리핑"""
        df = pd.DataFrame(
            {
                "EPS": [200000, -100000, 5000],
                "BPS": [10000, 10000, 50000],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        # A: ROE=2000% → 클리핑 100%, B: ROE=-1000% → 클리핑 -50%
        assert len(result) == 3

    def test_with_debt_ratio(self) -> None:
        df = pd.DataFrame(
            {"EPS": [5000, 3000], "BPS": [50000, 30000]},
            index=["A", "B"],
        )
        debt = pd.Series({"A": 50.0, "B": 200.0})

        result = self.factor.calculate(df, debt_ratio=debt)
        assert len(result) == 2
        # A가 부채비율 낮으므로 총합에서 유리

    def test_missing_eps_bps(self) -> None:
        df = pd.DataFrame({"PBR": [1.0, 2.0]}, index=["A", "B"])
        result = self.factor.calculate(df)
        assert result.empty

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["EPS", "BPS"])
        result = self.factor.calculate(df)
        assert result.empty

    def test_earnings_yield_component(self) -> None:
        """이익수익률(1/PER) 지표 포함 확인"""
        df = pd.DataFrame(
            {
                "EPS": [5000, 3000, 8000],
                "BPS": [50000, 30000, 40000],
                "PER": [5.0, 15.0, 10.0],
                "DIV": [2.0, 1.0, 3.0],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 3
        # A: PER=5 → EY=0.2 (최고), C: PER=10, B: PER=15 (최저)

    def test_dividend_component(self) -> None:
        """배당수익률 지표 포함 확인"""
        df = pd.DataFrame(
            {
                "EPS": [5000, 5000, 5000],
                "BPS": [50000, 50000, 50000],
                "PER": [10.0, 10.0, 10.0],
                "DIV": [0.0, 2.0, 5.0],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 3
        # C가 배당 가장 높으므로 퀄리티 스코어 최고
        assert result["C"] > result["A"]

    def test_quality_without_per_div(self) -> None:
        """PER/DIV 없이도 ROE만으로 계산 가능"""
        df = pd.DataFrame(
            {"EPS": [5000, 3000], "BPS": [50000, 30000]},
            index=["A", "B"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 2  # ROE만으로 작동


# ───────────────────────────────────────────────
# MultiFactorComposite 테스트
# ───────────────────────────────────────────────


class TestMultiFactorComposite:
    def setup_method(self) -> None:
        self.composite = MultiFactorComposite()

    def test_basic_calculation(self) -> None:
        value = pd.Series({"A": 90, "B": 70, "C": 50, "D": 30, "E": 10})
        momentum = pd.Series({"A": 80, "B": 60, "C": 40, "D": 20, "E": 100})
        quality = pd.Series({"A": 70, "B": 50, "C": 30, "D": 10, "E": 90})

        result = self.composite.calculate(value, momentum, quality)

        assert "composite_score" in result.columns
        assert len(result) == 5
        # 내림차순 정렬
        scores = result["composite_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_partial_factor_included(self) -> None:
        """2/3 이상 팩터가 있으면 포함 (가중치 재분배)"""
        value = pd.Series({"A": 90, "B": 70, "C": 50})
        momentum = pd.Series({"A": 80, "B": 60, "D": 40})
        quality = pd.Series({"A": 70, "C": 30, "D": 10})

        result = self.composite.calculate(value, momentum, quality)
        # A: 3/3 팩터, B: 2/3, C: 2/3, D: 2/3 → 모두 포함
        assert len(result) == 4
        assert "A" in result.index
        # A는 3팩터 모두 있으므로 가장 높은 스코어
        assert result.loc["A", "composite_score"] == result["composite_score"].max()

    def test_single_factor_excluded(self) -> None:
        """1/3 팩터만 있으면 제외"""
        value = pd.Series({"A": 90})
        momentum = pd.Series({"B": 80})
        quality = pd.Series({"C": 70})

        result = self.composite.calculate(value, momentum, quality)
        assert result.empty  # 각각 1개 팩터만 → 최소 2개 미충족

    def test_weights_applied(self) -> None:
        """가중치 적용 검증: V=0.4, M=0.4, Q=0.2"""
        value = pd.Series({"A": 100.0})
        momentum = pd.Series({"A": 50.0})
        quality = pd.Series({"A": 0.0})

        result = self.composite.calculate(value, momentum, quality)
        expected = 100.0 * 0.4 + 50.0 * 0.4 + 0.0 * 0.2
        assert abs(result.loc["A", "composite_score"] - expected) < 0.01

    def test_select_top(self) -> None:
        value = pd.Series({f"T{i}": float(100 - i) for i in range(50)})
        momentum = pd.Series({f"T{i}": float(50 + i) for i in range(50)})
        quality = pd.Series({f"T{i}": float(75) for i in range(50)})

        composite_df = self.composite.calculate(value, momentum, quality)
        selected = self.composite.select_top(composite_df, n=30)

        assert len(selected) == 30
        assert "weight" in selected.columns
        assert abs(selected["weight"].sum() - 1.0) < 0.01

    def test_apply_universe_filter(self) -> None:
        value = pd.Series({"A": 90, "B": 70, "C": 50, "D": 30})
        momentum = pd.Series({"A": 80, "B": 60, "C": 40, "D": 20})
        quality = pd.Series({"A": 70, "B": 50, "C": 30, "D": 10})

        composite_df = self.composite.calculate(value, momentum, quality)
        market_cap = pd.Series({"A": 1000, "B": 2000, "C": 3000, "D": 100})

        filtered = self.composite.apply_universe_filter(
            composite_df, market_cap, finance_tickers=["B"]
        )
        # D는 시가총액 하위 10%로 제외, B는 금융주 제외
        assert "B" not in filtered.index


# ───────────────────────────────────────────────
# 듀얼 모멘텀 (절대 모멘텀) 테스트
# ───────────────────────────────────────────────


class TestAbsoluteMomentum:
    def test_filters_below_risk_free_rate(self) -> None:
        """무위험 수익률 이하 종목 필터링"""
        returns = pd.Series({
            "A": 0.30,   # 통과 (30% > 3.5%)
            "B": 0.10,   # 통과
            "C": 0.02,   # 제거 (2% < 3.5%)
            "D": -0.05,  # 제거
            "E": 0.035,  # 제거 (동일은 미포함)
        })
        result = MomentumFactor.apply_absolute_momentum(returns, risk_free_rate=0.035)
        assert "A" in result.index
        assert "B" in result.index
        assert "C" not in result.index
        assert "D" not in result.index
        assert "E" not in result.index
        assert len(result) == 2

    def test_all_pass(self) -> None:
        """모든 종목 통과"""
        returns = pd.Series({"A": 0.20, "B": 0.15, "C": 0.10})
        result = MomentumFactor.apply_absolute_momentum(returns, risk_free_rate=0.035)
        assert len(result) == 3

    def test_all_filtered(self) -> None:
        """모든 종목 제거 (시장 전체 하락)"""
        returns = pd.Series({"A": -0.10, "B": -0.05, "C": 0.01})
        result = MomentumFactor.apply_absolute_momentum(returns, risk_free_rate=0.035)
        assert result.empty

    def test_empty_input(self) -> None:
        returns = pd.Series(dtype=float)
        result = MomentumFactor.apply_absolute_momentum(returns, risk_free_rate=0.035)
        assert result.empty

    def test_nan_handling(self) -> None:
        """NaN 값은 제거됨"""
        returns = pd.Series({"A": 0.20, "B": np.nan, "C": 0.10})
        result = MomentumFactor.apply_absolute_momentum(returns, risk_free_rate=0.035)
        assert "B" not in result.index
        assert len(result) == 2

    def test_default_risk_free_rate(self) -> None:
        """기본 무위험 수익률(settings) 사용"""
        returns = pd.Series({"A": 0.20, "B": 0.01})
        result = MomentumFactor.apply_absolute_momentum(returns)
        assert "A" in result.index
        # B는 기본 3.5% 미만이므로 제거
        assert "B" not in result.index


# ───────────────────────────────────────────────
# F-Score 테스트
# ───────────────────────────────────────────────


class TestFScore:
    def test_perfect_score(self) -> None:
        """우량주: ROE 양수 + 중앙값 초과, PER 양수, DIV 양수, PBR 중앙값 미만"""
        df = pd.DataFrame(
            {
                "EPS": [5000, 1000, 500, 100, 50],
                "BPS": [25000, 25000, 25000, 25000, 25000],
                "PER": [5.0, 25.0, 50.0, 100.0, 200.0],
                "PBR": [0.3, 0.8, 1.2, 2.0, 5.0],
                "DIV": [5.0, 3.0, 1.0, 0.5, 0.0],
            },
            index=["A", "B", "C", "D", "E"],
        )
        fscore = QualityFactor.calc_fscore(df)
        assert fscore.name == "fscore"
        assert len(fscore) == 5
        # A: ROE>0(+1), ROE>median(+1), PER>0(+1), DIV>0(+1), PBR<median(+1) = 5
        assert fscore["A"] == 5
        # E: ROE>0(+1), ROE<median(0), PER>0(+1), DIV=0(0), PBR>median(0) = 2
        assert fscore["E"] == 2

    def test_loss_making_company(self) -> None:
        """적자 기업은 낮은 F-Score"""
        df = pd.DataFrame(
            {
                "EPS": [-500, 5000],
                "BPS": [25000, 25000],
                "PER": [-50.0, 5.0],
                "PBR": [0.8, 0.8],
                "DIV": [0.0, 3.0],
            },
            index=["LOSS", "GOOD"],
        )
        fscore = QualityFactor.calc_fscore(df)
        assert fscore["LOSS"] < fscore["GOOD"]
        # LOSS: ROE<0(0), ROE<median(0), PER<0(0), DIV=0(0), PBR<median 비해당(0) = 0
        assert fscore["LOSS"] <= 1

    def test_filter_removes_low_fscore(self) -> None:
        """min_fscore 미만 종목 제거"""
        df = pd.DataFrame(
            {
                "EPS": [5000, -500, 1000, 100],
                "BPS": [25000, 25000, 25000, 25000],
                "PER": [5.0, -50.0, 25.0, 250.0],
                "PBR": [0.3, 3.0, 0.8, 5.0],
                "DIV": [3.0, 0.0, 2.0, 0.0],
            },
            index=["A", "B", "C", "D"],
        )
        fscore = QualityFactor.calc_fscore(df)
        filtered = QualityFactor.apply_fscore_filter(df, fscore, min_fscore=3)
        # 낮은 F-Score 종목은 제거됨
        assert len(filtered) < len(df)
        # A는 우량주이므로 반드시 포함
        assert "A" in filtered.index

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["EPS", "BPS", "PER", "PBR", "DIV"])
        fscore = QualityFactor.calc_fscore(df)
        assert fscore.empty

    def test_partial_columns(self) -> None:
        """일부 컬럼만 있어도 동작 (가능한 항목만 계산)"""
        df = pd.DataFrame(
            {"EPS": [5000, -500], "BPS": [25000, 25000]},
            index=["A", "B"],
        )
        fscore = QualityFactor.calc_fscore(df)
        assert len(fscore) == 2
        # A: ROE>0(+1), ROE>median(+1) = 2 (PER/DIV/PBR 없음)
        assert fscore["A"] == 2
        assert fscore["B"] == 0

    def test_filter_with_empty_fscore(self) -> None:
        """빈 F-Score는 필터 미적용"""
        df = pd.DataFrame({"EPS": [5000]}, index=["A"])
        fscore = pd.Series(dtype=int, name="fscore")
        filtered = QualityFactor.apply_fscore_filter(df, fscore, min_fscore=3)
        assert len(filtered) == 1


# ───────────────────────────────────────────────
# 변동성 필터 테스트
# ───────────────────────────────────────────────


class TestVolatilityFilter:
    """screener._apply_volatility_filter 단위 테스트"""

    def setup_method(self) -> None:
        from unittest.mock import MagicMock
        from strategy.screener import MultiFactorScreener

        self.screener = MultiFactorScreener.__new__(MultiFactorScreener)
        self.screener.collector = MagicMock()

    def _make_ohlcv(self, daily_vol: float, n_days: int = 200) -> pd.DataFrame:
        """지정된 일별 변동성으로 가상 OHLCV 생성"""
        np.random.seed(42)
        returns = np.random.normal(0, daily_vol, n_days)
        prices = 10000 * np.exp(np.cumsum(returns))
        dates = pd.bdate_range("2023-01-01", periods=n_days)
        return pd.DataFrame({"close": prices}, index=dates)

    def test_high_vol_excluded(self) -> None:
        """고변동성 종목이 제외되는지 확인"""
        from config.settings import settings

        settings.volatility.filter_enabled = True
        settings.volatility.max_percentile = 60.0  # 상위 40% 제외 (엄격)

        # A: 낮은 변동성, B: 높은 변동성, C: 중간
        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            vol_map = {"A": 0.01, "B": 0.08, "C": 0.03}
            return self._make_ohlcv(vol_map.get(ticker, 0.03))

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")

        assert "A" in result  # 낮은 변동성 — 통과
        assert "B" not in result  # 높은 변동성 — 제외
        # 복원
        settings.volatility.max_percentile = 80.0

    def test_all_pass_when_disabled(self) -> None:
        """비활성화 시 전체 통과"""
        from config.settings import settings

        settings.volatility.filter_enabled = False
        # filter_enabled=False이면 screener.screen()에서 아예 호출 안함
        # 직접 호출 시에도 정상 동작 확인
        settings.volatility.filter_enabled = True
        settings.volatility.max_percentile = 100.0  # 100% = 아무도 제외 안함

        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            return self._make_ohlcv(0.05)

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")
        assert len(result) == 3
        settings.volatility.max_percentile = 80.0

    def test_empty_tickers(self) -> None:
        """빈 종목 리스트"""
        result = self.screener._apply_volatility_filter([], "20240101")
        assert result == []

    def test_no_data_tickers_preserved(self) -> None:
        """데이터 없는 종목은 유지 (제외하지 않음)"""
        from config.settings import settings

        settings.volatility.filter_enabled = True

        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            if ticker == "B":
                return pd.DataFrame()  # 데이터 없음
            return self._make_ohlcv(0.02)

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")
        assert "B" in result  # 데이터 없는 종목은 유지

    def test_insufficient_data_preserved(self) -> None:
        """데이터 부족 종목은 유지"""
        from config.settings import settings

        settings.volatility.filter_enabled = True

        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            if ticker == "SHORT":
                return self._make_ohlcv(0.05, n_days=10)  # 너무 짧음
            return self._make_ohlcv(0.02, n_days=200)

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(
            ["A", "SHORT", "C"], "20240101"
        )
        assert "SHORT" in result  # 데이터 부족 종목은 유지
