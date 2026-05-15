# tests/test_factors.py
import numpy as np
import pandas as pd
import pytest

from config.settings import settings
from factors.composite import MultiFactorComposite
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.value import ValueFactor

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
                "PCR": [3.0, 6.0, 9.0, 12.0, 15.0],
                "DIV": [4.0, 3.0, 2.0, 1.0, 0.5],
            },
            index=["A", "B", "C", "D", "E"],
        )
        result = self.factor.calculate(df)

        assert result.name == "value_score"
        assert len(result) == 5
        # PBR 낮고, PCR 낮고, DIV 높은 A가 최고 스코어
        assert result.index[0] == "A"
        # 모든 스코어가 0~100 범위
        assert result.min() >= 0
        assert result.max() <= 100

    def test_negative_pbr_included_via_other_factors(self) -> None:
        """PBR <= 0인 종목도 PCR+DIV로 스코어 산출 (union 방식)"""
        df = pd.DataFrame(
            {
                "PBR": [-1.0, 0.0, 1.0, 2.0],
                "PCR": [5.0, 5.0, 5.0, 5.0],
                "DIV": [1.0, 1.0, 1.0, 1.0],
            },
            index=["A", "B", "C", "D"],
        )
        result = self.factor.calculate(df)
        # PBR <= 0인 A, B도 PCR+DIV 스코어로 포함됨
        assert "A" in result.index
        assert "B" in result.index
        # PBR이 유효한 C, D는 3개 지표 모두 반영
        assert "C" in result.index
        assert "D" in result.index

    def test_negative_pcr_excluded_via_other_factors(self) -> None:
        """영업CF 마이너스(PCR <= 0)도 PBR+DIV로 스코어 산출 (union 방식)"""
        df = pd.DataFrame(
            {
                "PBR": [1.0, 1.0, 1.0],
                "PCR": [-5.0, 0.0, 10.0],
                "DIV": [1.0, 1.0, 1.0],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        # PCR이 무효해도 PBR+DIV로 스코어 산출
        assert "A" in result.index
        assert "B" in result.index
        assert "C" in result.index

    def test_pcr_positive_inverse_ranking(self) -> None:
        """PCR 양수일 때 역수 변환 + 순위 정상 동작"""
        df = pd.DataFrame(
            {
                "PCR": [3.0, 6.0, 12.0, 24.0, 48.0],
            },
            index=["A", "B", "C", "D", "E"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 5
        # PCR 낮을수록 고득점 → A가 최고
        assert result.idxmax() == "A"
        assert result.idxmin() == "E"

    def test_pcr_zero_or_negative_excluded(self) -> None:
        """PCR 0 이하 종목은 PCR 스코어에서 제외"""
        df = pd.DataFrame(
            {
                "PCR": [-5.0, 0.0, 5.0, 10.0],
            },
            index=["A", "B", "C", "D"],
        )
        result = self.factor.calculate(df)
        # PCR <= 0인 A, B는 PCR만 있으므로 제외됨
        assert "A" not in result.index
        assert "B" not in result.index
        assert "C" in result.index
        assert "D" in result.index

    def test_pcr_missing_reweights_to_pbr_div(self) -> None:
        """PCR 결측 시 PBR+DIV 가중치 재분배 확인"""
        df = pd.DataFrame(
            {
                "PBR": [0.5, 1.0, 2.0],
                "PCR": [float("nan"), float("nan"), float("nan")],
                "DIV": [4.0, 2.0, 1.0],
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        # PCR이 모두 NaN이면 PBR+DIV만으로 스코어 산출
        assert len(result) == 3
        assert result.name == "value_score"
        # A가 PBR 낮고 DIV 높으므로 최고
        assert result.idxmax() == "A"

    def test_empty_input(self) -> None:
        df = pd.DataFrame(columns=["PBR", "PCR", "DIV"])
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

    def test_basic_gpa_calculation(self) -> None:
        """GP/A 기반 퀄리티 스코어 계산"""
        df = pd.DataFrame(
            {
                "GROSS_PROFIT": [5e9, 3e9, 1e9, 8e9, 2e9],
                "TOTAL_ASSETS": [10e9, 10e9, 10e9, 10e9, 10e9],
                "EPS": [5000, 3000, 1000, 8000, 2000],
                "BPS": [50000, 30000, 20000, 40000, 25000],
                "PER": [10.0, 15.0, 25.0, 5.0, 20.0],
            },
            index=["A", "B", "C", "D", "E"],
        )
        result = self.factor.calculate(df)

        assert result.name == "quality_score"
        assert len(result) == 5

    def test_total_assets_zero_excluded(self) -> None:
        """총자산 <= 0인 종목 제외"""
        df = pd.DataFrame(
            {
                "GROSS_PROFIT": [5e9, 3e9, 1e9],
                "TOTAL_ASSETS": [-1e9, 0, 10e9],
            },
            index=["A", "B", "C"],
        )
        gpa = QualityFactor._calc_gpa_score(df)
        assert "A" not in gpa.index
        assert "B" not in gpa.index
        assert "C" in gpa.index

    def test_gpa_ranking(self) -> None:
        """GP/A 높을수록 높은 스코어"""
        df = pd.DataFrame(
            {
                "GROSS_PROFIT": [1e9, 5e9, 10e9],
                "TOTAL_ASSETS": [10e9, 10e9, 10e9],
            },
            index=["LOW", "MID", "HIGH"],
        )
        gpa = QualityFactor._calc_gpa_score(df)
        assert gpa["HIGH"] > gpa["LOW"]

    def test_gpa_missing_falls_back_to_roe(self) -> None:
        """GROSS_PROFIT/TOTAL_ASSETS 없으면 ROE 폴백"""
        df = pd.DataFrame(
            {"EPS": [5000, 3000], "BPS": [50000, 30000]},
            index=["A", "B"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 2  # ROE 폴백으로 작동

    def test_with_debt_ratio(self) -> None:
        df = pd.DataFrame(
            {"EPS": [5000, 3000], "BPS": [50000, 30000]},
            index=["A", "B"],
        )
        debt = pd.Series({"A": 50.0, "B": 200.0})

        result = self.factor.calculate(df, debt_ratio=debt)
        assert len(result) == 2

    def test_no_quality_columns_uses_fscore(self) -> None:
        """GP/A·EY 데이터 없어도 F-Score(0점)로 스코어 산출"""
        df = pd.DataFrame({"PBR": [1.0, 2.0]}, index=["A", "B"])
        result = self.factor.calculate(df)
        # F-Score가 PBR 기반으로 계산되므로 결과 존재
        assert len(result) == 2

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
            },
            index=["A", "B", "C"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 3

    def test_fscore_normalized_in_quality(self) -> None:
        """F-Score가 0~100으로 정규화되어 퀄리티 스코어에 포함"""
        df = pd.DataFrame(
            {
                "EPS": [5000, 1000, -500],
                "BPS": [25000, 25000, 25000],
                "PER": [5.0, 25.0, -10.0],
                "PBR": [0.3, 0.8, 3.0],
                "DIV": [5.0, 1.0, 0.0],
            },
            index=["GOOD", "MID", "BAD"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 3
        # GOOD: F-Score 최고 → 퀄리티 높아야 함
        assert result["GOOD"] > result["BAD"]

    def test_quality_without_per(self) -> None:
        """PER 없이도 GP/A + F-Score로 계산 가능"""
        df = pd.DataFrame(
            {
                "GROSS_PROFIT": [5e9, 3e9],
                "TOTAL_ASSETS": [10e9, 10e9],
                "EPS": [5000, 3000],
                "BPS": [50000, 30000],
            },
            index=["A", "B"],
        )
        result = self.factor.calculate(df)
        assert len(result) == 2


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
        """가중치 적용 검증: settings의 실제 가중치 사용"""
        value = pd.Series({"A": 100.0})
        momentum = pd.Series({"A": 50.0})
        quality = pd.Series({"A": 0.0})

        result = self.composite.calculate(value, momentum, quality)
        w = settings.factor_weights
        expected = 100.0 * w.value + 50.0 * w.momentum + 0.0 * w.quality
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
# 본업 품질 필터 (Step 1) 테스트
# ───────────────────────────────────────────────


class TestClassifyByKsic:
    """factors.composite.classify_by_ksic — KSIC 코드 매핑 검증 (S4-A 보강)"""

    def test_electronics_3digit(self) -> None:
        from factors.composite import classify_by_ksic
        # 삼성전자 induty_code='264' → "26" 전자·IT
        sector, is_fin = classify_by_ksic("264")
        assert sector == "전자·IT"
        assert is_fin is False

    def test_finance_5digit(self) -> None:
        from factors.composite import classify_by_ksic
        # 신한지주 induty_code='64992' → "64" 금융업
        sector, is_fin = classify_by_ksic("64992")
        assert sector == "금융업"
        assert is_fin is True

    def test_insurance(self) -> None:
        from factors.composite import classify_by_ksic
        # 삼성생명 induty_code='65110' → "65" 보험
        sector, is_fin = classify_by_ksic("65110")
        assert sector == "보험"
        assert is_fin is True

    def test_bank(self) -> None:
        from factors.composite import classify_by_ksic
        # 기업은행 induty_code='64121' → "64" 금융업
        sector, is_fin = classify_by_ksic("64121")
        assert sector == "금융업"
        assert is_fin is True

    def test_unknown_code_returns_etc(self) -> None:
        from factors.composite import classify_by_ksic
        sector, is_fin = classify_by_ksic("99999")
        assert sector == "기타"
        assert is_fin is False

    def test_empty_code(self) -> None:
        from factors.composite import classify_by_ksic
        sector, is_fin = classify_by_ksic("")
        assert sector is None
        assert is_fin is False


class TestClassifyFinancialByName:
    """factors.composite.classify_financial_by_name 휴리스틱 검증 (S4-A)"""

    def test_bank_in_name(self) -> None:
        from factors.composite import classify_financial_by_name
        is_fin, sec = classify_financial_by_name("024110", "기업은행")
        assert is_fin
        assert sec == "은행"

    def test_securities_in_name(self) -> None:
        from factors.composite import classify_financial_by_name
        is_fin, sec = classify_financial_by_name("030200", "미래에셋증권")
        assert is_fin
        assert sec == "증권"

    def test_life_insurance(self) -> None:
        from factors.composite import classify_financial_by_name
        is_fin, sec = classify_financial_by_name("032830", "삼성생명")
        assert is_fin
        assert sec == "보험"

    def test_financial_holdings(self) -> None:
        from factors.composite import classify_financial_by_name
        is_fin, sec = classify_financial_by_name("105560", "KB금융지주")
        assert is_fin
        assert sec == "금융업"

    def test_whitelist_shinhan(self) -> None:
        """신한지주는 키워드 매칭 안 되지만 화이트리스트로 처리"""
        from factors.composite import classify_financial_by_name
        is_fin, sec = classify_financial_by_name("055550", "신한지주")
        assert is_fin
        assert sec == "금융업"

    def test_non_financial(self) -> None:
        from factors.composite import classify_financial_by_name
        for ticker, name in [
            ("005930", "삼성전자"),
            ("000660", "SK하이닉스"),
            ("035420", "NAVER"),
        ]:
            is_fin, sec = classify_financial_by_name(ticker, name)
            assert not is_fin
            assert sec is None


class TestDebtRatioFilter:
    """apply_debt_ratio_filter — 부채비율 상한 + 자본잠식 (S2)"""

    def test_under_threshold_passes(self) -> None:
        """부채비율 150% < max=200 → 통과"""
        df = pd.DataFrame({
            "DEBT_RATIO": [150.0],
            "TOTAL_EQUITY": [1e10],
        }, index=["A"])
        result = QualityFactor.apply_debt_ratio_filter(
            df, max_debt_ratio=200.0, exclude_capital_impairment=True,
        )
        assert "A" in result.index

    def test_over_threshold_removed(self) -> None:
        """부채비율 250% > max=200 → 제거"""
        df = pd.DataFrame({
            "DEBT_RATIO": [250.0],
            "TOTAL_EQUITY": [1e10],
        }, index=["B"])
        result = QualityFactor.apply_debt_ratio_filter(
            df, max_debt_ratio=200.0, exclude_capital_impairment=True,
        )
        assert "B" not in result.index

    def test_nan_debt_ratio_passes(self) -> None:
        """DEBT_RATIO=NaN → 통과 (데이터 없음, 보수 정책)"""
        df = pd.DataFrame({
            "DEBT_RATIO": [np.nan],
            "TOTAL_EQUITY": [1e10],
        }, index=["NODATA"])
        result = QualityFactor.apply_debt_ratio_filter(
            df, max_debt_ratio=200.0, exclude_capital_impairment=True,
        )
        assert "NODATA" in result.index

    def test_capital_impairment_removed(self) -> None:
        """자본잠식 (TOTAL_EQUITY <= 0) → 제거 (exclude_capital_impairment=True)"""
        df = pd.DataFrame({
            "DEBT_RATIO": [np.nan],  # 자본잠식이면 debt_ratio 계산 불가
            "TOTAL_EQUITY": [-1e8],
        }, index=["IMPAIRED"])
        result = QualityFactor.apply_debt_ratio_filter(
            df, max_debt_ratio=200.0, exclude_capital_impairment=True,
        )
        assert "IMPAIRED" not in result.index


class TestConsecutiveProfitFilter:
    """apply_consecutive_profit_filter — 연속 흑자 N분기 필터 (Step 3)"""

    @staticmethod
    def _mock_storage(per_ticker_quarters: dict) -> object:
        """ticker → DataFrame 매핑을 받아 load_fundamentals_quarterly를 mock.

        Args:
            per_ticker_quarters: {ticker: DataFrame(columns include operating_income)}
        """
        from unittest.mock import MagicMock

        store = MagicMock()
        def _load(ticker, as_of_date, n_quarters=4):
            return per_ticker_quarters.get(ticker, pd.DataFrame())
        store.load_fundamentals_quarterly = _load
        return store

    def test_all_four_positive_passes(self) -> None:
        """4분기 모두 영업이익 양수 → 통과"""
        from datetime import date as _date

        store = self._mock_storage({
            "AAA": pd.DataFrame({
                "bsns_year": ["2024", "2023", "2023", "2023"],
                "reprt_code": ["11011", "11014", "11012", "11013"],
                "operating_income": [1e9, 8e8, 5e8, 4e8],
            }),
        })
        fund = pd.DataFrame({"PBR": [1.0]}, index=["AAA"])
        result = QualityFactor.apply_consecutive_profit_filter(
            fund, store, as_of_date=_date(2025, 4, 15),
        )
        assert "AAA" in result.index

    def test_one_negative_quarter_removed(self) -> None:
        """4분기 중 1개 음수 → require_all_positive=True 면 제외"""
        from datetime import date as _date

        store = self._mock_storage({
            "BAD": pd.DataFrame({
                "bsns_year": ["2024", "2023", "2023", "2023"],
                "reprt_code": ["11011", "11014", "11012", "11013"],
                "operating_income": [1e9, -2e8, 5e8, 4e8],  # Q3 음수
            }),
        })
        fund = pd.DataFrame({"PBR": [1.0]}, index=["BAD"])
        result = QualityFactor.apply_consecutive_profit_filter(
            fund, store, as_of_date=_date(2025, 4, 15),
        )
        assert "BAD" not in result.index

    def test_three_quarters_data_passes_min_data_3(self) -> None:
        """데이터 3분기 + min_data=3 → 필터 적용. 모두 양수면 통과"""
        from datetime import date as _date

        store = self._mock_storage({
            "OK": pd.DataFrame({
                "bsns_year": ["2024", "2023", "2023"],
                "reprt_code": ["11011", "11014", "11012"],
                "operating_income": [1e9, 5e8, 3e8],
            }),
        })
        fund = pd.DataFrame({"PBR": [1.0]}, index=["OK"])
        result = QualityFactor.apply_consecutive_profit_filter(
            fund, store, as_of_date=_date(2025, 4, 15),
            min_data_quarters=3,
        )
        assert "OK" in result.index

    def test_two_quarters_data_passes_due_to_insufficient(self) -> None:
        """데이터 2분기 + min_data=3 → 데이터 부족으로 통과 (보수적)"""
        from datetime import date as _date

        store = self._mock_storage({
            "LOSS": pd.DataFrame({
                "bsns_year": ["2024", "2023"],
                "reprt_code": ["11011", "11014"],
                "operating_income": [-1e9, -5e8],  # 둘 다 음수지만 데이터 부족
            }),
        })
        fund = pd.DataFrame({"PBR": [1.0]}, index=["LOSS"])
        result = QualityFactor.apply_consecutive_profit_filter(
            fund, store, as_of_date=_date(2025, 4, 15),
            min_data_quarters=3,
        )
        # 2분기 < min_data 3 이므로 필터 미적용 → 통과
        assert "LOSS" in result.index

    def test_005620_scenario_removed(self) -> None:
        """005620 시나리오: 2017-06-30 PIT에서 4분기 중 3분기 영업적자 → 제외"""
        from datetime import date as _date

        # 실제 DB 백필 결과 그대로 mock
        store = self._mock_storage({
            "005620": pd.DataFrame({
                "bsns_year": ["2017", "2016", "2016", "2016"],
                "reprt_code": ["11013", "11011", "11014", "11012"],
                "operating_income": [
                    807178945,         # 2017-Q1 흑자 전환
                    -19805979906,      # 2016 Annual 대규모 적자
                    -721015514,        # 2016 Q3 적자
                    -7237165374,       # 2016 Half 대규모 적자
                ],
            }),
        })
        fund = pd.DataFrame({"PBR": [0.27]}, index=["005620"])
        result = QualityFactor.apply_consecutive_profit_filter(
            fund, store, as_of_date=_date(2017, 6, 30),
            n_quarters=4, metric="operating_income",
            require_all_positive=True, min_data_quarters=3,
        )
        # 005620은 명확히 제외되어야 함
        assert "005620" not in result.index


class TestOperatingQualityFilter:
    """apply_operating_quality_filter — 영업이익/매출/영업CF 양수 필터 검증"""

    def test_negative_op_income_removed(self) -> None:
        """영업이익 음수 종목은 제거된다"""
        df = pd.DataFrame(
            {
                "OPERATING_INCOME": [1e9, -5e8, 3e9],
                "REVENUE": [10e9, 10e9, 10e9],
                "PCR": [5.0, 5.0, 5.0],
            },
            index=["GOOD", "LOSS", "OK"],
        )
        result = QualityFactor.apply_operating_quality_filter(
            df,
            require_op_income_positive=True,
            require_revenue_positive=False,
            require_op_cf_positive_if_available=False,
        )
        assert "GOOD" in result.index
        assert "OK" in result.index
        assert "LOSS" not in result.index

    def test_nan_op_income_passes(self) -> None:
        """영업이익이 NaN인 개별 종목은 데이터 없음으로 간주, 통과한다"""
        df = pd.DataFrame(
            {
                "OPERATING_INCOME": [1e9, np.nan, -5e8],
                "REVENUE": [10e9, 10e9, 10e9],
                "PCR": [5.0, 5.0, 5.0],
            },
            index=["GOOD", "UNKNOWN", "LOSS"],
        )
        result = QualityFactor.apply_operating_quality_filter(
            df,
            require_op_income_positive=True,
            require_revenue_positive=False,
            require_op_cf_positive_if_available=False,
        )
        assert "GOOD" in result.index
        assert "UNKNOWN" in result.index  # NaN은 통과 (음수 아님)
        assert "LOSS" not in result.index

    def test_nan_pcr_passes_op_cf_step(self) -> None:
        """PCR=NaN 종목은 영업CF 필터에 영향받지 않고 통과한다"""
        df = pd.DataFrame(
            {
                "OPERATING_INCOME": [1e9, 1e9, 1e9],
                "REVENUE": [10e9, 10e9, 10e9],
                "PCR": [np.nan, 5.0, -3.0],
            },
            index=["NOCF", "WITHCF", "NEGCF"],
        )
        result = QualityFactor.apply_operating_quality_filter(
            df,
            require_op_income_positive=False,
            require_revenue_positive=False,
            require_op_cf_positive_if_available=True,
        )
        assert "NOCF" in result.index  # PCR NaN은 통과
        assert "WITHCF" in result.index  # PCR 양수 통과
        assert "NEGCF" not in result.index  # PCR 음수는 제거


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

    def test_high_vol_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """고변동성 종목이 제외되는지 확인"""
        from config.settings import settings

        monkeypatch.setattr(settings.volatility, "filter_enabled", True)
        monkeypatch.setattr(settings.volatility, "max_percentile", 60.0)  # 상위 40% 제외 (엄격)

        # A: 낮은 변동성, B: 높은 변동성, C: 중간
        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            vol_map = {"A": 0.01, "B": 0.08, "C": 0.03}
            return self._make_ohlcv(vol_map.get(ticker, 0.03))

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")

        assert "A" in result  # 낮은 변동성 — 통과
        assert "B" not in result  # 높은 변동성 — 제외

    def test_all_pass_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """비활성화 시 전체 통과"""
        from config.settings import settings

        monkeypatch.setattr(settings.volatility, "filter_enabled", True)
        monkeypatch.setattr(settings.volatility, "max_percentile", 100.0)  # 100% = 아무도 제외 안함

        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            return self._make_ohlcv(0.05)

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")
        assert len(result) == 3

    def test_empty_tickers(self) -> None:
        """빈 종목 리스트"""
        result = self.screener._apply_volatility_filter([], "20240101")
        assert result == []

    def test_no_data_tickers_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """데이터 없는 종목은 유지 (제외하지 않음)"""
        from config.settings import settings

        monkeypatch.setattr(settings.volatility, "filter_enabled", True)

        def mock_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
            if ticker == "B":
                return pd.DataFrame()  # 데이터 없음
            return self._make_ohlcv(0.02)

        self.screener.collector.get_ohlcv = mock_ohlcv
        self.screener.collector.get_ticker_name = lambda t: t

        result = self.screener._apply_volatility_filter(["A", "B", "C"], "20240101")
        assert "B" in result  # 데이터 없는 종목은 유지

    def test_insufficient_data_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """데이터 부족 종목은 유지"""
        from config.settings import settings

        monkeypatch.setattr(settings.volatility, "filter_enabled", True)

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
