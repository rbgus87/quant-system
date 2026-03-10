# backtest/metrics.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

RF_ANNUAL = 0.03  # 무위험 수익률 연 3% (한국 국고채 기준)


class PerformanceAnalyzer:
    """백테스트 성과 분석"""

    def calculate_cagr(self, portfolio_values: pd.Series) -> float:
        """연 복합 수익률 (CAGR)

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series (index=date)

        Returns:
            CAGR (소수점, 예: 0.15 = 15%)
        """
        if len(portfolio_values) < 2:
            return 0.0

        total_return = portfolio_values.iloc[-1] / portfolio_values.iloc[0]
        n_days = (portfolio_values.index[-1] - portfolio_values.index[0]).days
        n_years = n_days / 365.25 if n_days > 0 else len(portfolio_values) / 252

        if n_years <= 0 or total_return <= 0:
            return 0.0

        return float(total_return ** (1 / n_years) - 1)

    def calculate_mdd(self, portfolio_values: pd.Series) -> float:
        """최대 낙폭 (Maximum Drawdown)

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series

        Returns:
            MDD (음수, 예: -0.20 = -20%)
        """
        if len(portfolio_values) < 2:
            return 0.0

        rolling_max = portfolio_values.cummax()
        drawdown = (portfolio_values - rolling_max) / rolling_max
        return float(drawdown.min())

    def calculate_sharpe(
        self, returns: pd.Series, risk_free: float = RF_ANNUAL
    ) -> float:
        """샤프 비율 (연환산)

        Args:
            returns: 일별 수익률 Series
            risk_free: 연간 무위험 수익률

        Returns:
            샤프 비율 (float)
        """
        if len(returns) < 2:
            return 0.0

        rf_daily = risk_free / 252
        excess = returns - rf_daily
        std = excess.std()
        if std < 1e-10:
            return 0.0
        return float((excess.mean() / std) * np.sqrt(252))

    def calculate_calmar(self, cagr: float, mdd: float) -> float:
        """칼마 비율 (CAGR / |MDD|)

        Args:
            cagr: 연 복합 수익률
            mdd: 최대 낙폭 (음수)

        Returns:
            칼마 비율
        """
        if mdd == 0.0:
            return 0.0
        return float(cagr / abs(mdd))

    def calculate_win_rate(self, returns: pd.Series) -> float:
        """일 승률 (양수 수익 비율)

        Args:
            returns: 일별 수익률 Series

        Returns:
            승률 (0~1)
        """
        if len(returns) == 0:
            return 0.0
        return float((returns > 0).mean())

    def calculate_volatility(self, returns: pd.Series) -> float:
        """연환산 변동성

        Args:
            returns: 일별 수익률 Series

        Returns:
            연환산 변동성 (소수점)
        """
        if len(returns) < 2:
            return 0.0
        return float(returns.std() * np.sqrt(252))

    def calculate_sortino(
        self, returns: pd.Series, risk_free: float = RF_ANNUAL
    ) -> float:
        """소르티노 비율 (하방 변동성 기반)

        Args:
            returns: 일별 수익률 Series
            risk_free: 연간 무위험 수익률

        Returns:
            소르티노 비율
        """
        if len(returns) < 2:
            return 0.0

        rf_daily = risk_free / 252
        excess = returns - rf_daily
        # 교과서 방식: 전체 관측치 대비 하방 편차 (음수만 사용, 양수는 0으로)
        downside_diff = excess.clip(upper=0)
        if (downside_diff == 0).all():
            return 0.0
        downside_std = float(np.sqrt((downside_diff**2).mean()))
        if downside_std < 1e-10:
            return 0.0
        return float((excess.mean() / downside_std) * np.sqrt(252))

    def calculate_var(
        self, returns: pd.Series, confidence: float = 0.95
    ) -> float:
        """일별 VaR (Value at Risk)

        Args:
            returns: 일별 수익률 Series
            confidence: 신뢰수준 (기본 95%)

        Returns:
            VaR 값 (음수, 예: -0.02 = 일 최대 2% 손실)
        """
        if len(returns) < 10:
            return 0.0
        return float(returns.quantile(1 - confidence))

    def calculate_excess_return(
        self,
        portfolio_values: pd.Series,
        benchmark_values: pd.Series,
    ) -> float:
        """벤치마크 대비 초과수익률 (연환산)

        Args:
            portfolio_values: 포트폴리오 가치 Series
            benchmark_values: 벤치마크 가치 Series (동일 인덱스)

        Returns:
            초과 CAGR (소수점)
        """
        port_cagr = self.calculate_cagr(portfolio_values)
        bm_cagr = self.calculate_cagr(benchmark_values)
        return port_cagr - bm_cagr

    def calculate_information_ratio(
        self,
        returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> float:
        """정보 비율 (Information Ratio)

        초과수익률 / 추적오차

        Args:
            returns: 포트폴리오 일별 수익률
            benchmark_returns: 벤치마크 일별 수익률

        Returns:
            IR 값
        """
        if len(returns) < 10 or len(benchmark_returns) < 10:
            return 0.0

        # 공통 인덱스
        common = returns.index.intersection(benchmark_returns.index)
        if len(common) < 10:
            return 0.0

        excess = returns.reindex(common) - benchmark_returns.reindex(common)
        tracking_error = float(excess.std() * np.sqrt(252))
        if tracking_error < 1e-10:
            return 0.0
        return float(excess.mean() * 252 / tracking_error)

    def monthly_returns(self, portfolio_values: pd.Series) -> pd.DataFrame:
        """월별 수익률 테이블 (행=연도, 열=월)

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series (index=date)

        Returns:
            DataFrame (index=연도, columns=1~12월, 값=수익률)
        """
        if len(portfolio_values) < 2:
            return pd.DataFrame()

        # 월말 기준 리샘플링
        monthly = portfolio_values.resample("ME").last()
        monthly_ret = monthly.pct_change().dropna()

        if monthly_ret.empty:
            return pd.DataFrame()

        # 연도-월 피벗
        df = pd.DataFrame({
            "year": monthly_ret.index.year,
            "month": monthly_ret.index.month,
            "return": monthly_ret.values,
        })
        pivot = df.pivot_table(index="year", columns="month", values="return")
        pivot.columns = [f"{m}월" for m in pivot.columns]

        # 연간 합산 수익률 (복리)
        yearly = portfolio_values.resample("YE").last()
        yearly_ret = yearly.pct_change().dropna()
        if not yearly_ret.empty:
            yearly_map = dict(zip(yearly_ret.index.year, yearly_ret.values))
            pivot["연간"] = pivot.index.map(lambda y: yearly_map.get(y, np.nan))

        return pivot

    def yearly_returns(self, portfolio_values: pd.Series) -> pd.Series:
        """연도별 수익률 Series

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series

        Returns:
            Series (index=연도, 값=수익률)
        """
        if len(portfolio_values) < 2:
            return pd.Series(dtype=float)

        yearly = portfolio_values.resample("YE").last()
        ret = yearly.pct_change().dropna()
        ret.index = ret.index.year
        ret.name = "yearly_return"
        return ret

    def calculate_mdd_recovery_days(self, portfolio_values: pd.Series) -> int:
        """MDD 회복 기간 — 고점 회복까지 걸린 거래일 수

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series

        Returns:
            MDD 시작일부터 고점 회복(또는 마지막 날)까지의 거래일 수.
            회복 못하면 마지막 날까지의 거래일 수 반환.
        """
        if len(portfolio_values) < 2:
            return 0

        rolling_max = portfolio_values.cummax()
        drawdown = (portfolio_values - rolling_max) / rolling_max
        mdd_idx = drawdown.idxmin()

        # MDD 발생 이후 고점 회복 시점 찾기
        peak_at_mdd = rolling_max.loc[mdd_idx]
        after_mdd = portfolio_values.loc[mdd_idx:]

        recovered = after_mdd[after_mdd >= peak_at_mdd]
        if len(recovered) > 1:
            # 첫 번째는 MDD 당일일 수 있으므로 두 번째부터 확인
            recovery_candidates = recovered.iloc[1:]
            if not recovery_candidates.empty:
                recovery_date = recovery_candidates.index[0]
            else:
                recovery_date = portfolio_values.index[-1]
        else:
            recovery_date = portfolio_values.index[-1]

        # 거래일 수 계산
        mdd_pos = portfolio_values.index.get_loc(mdd_idx)
        recovery_pos = portfolio_values.index.get_loc(recovery_date)
        return int(recovery_pos - mdd_pos)

    def factor_attribution(
        self,
        composite_df: pd.DataFrame,
        returns: pd.Series,
    ) -> dict[str, float]:
        """팩터 귀인 분석 — 각 팩터의 포트폴리오 성과 기여도

        포트폴리오 내 종목들의 팩터 스코어와 실현 수익률 간 상관도를
        기반으로 각 팩터의 기여도를 추정합니다.

        Args:
            composite_df: MultiFactorComposite.calculate() 결과
                (columns: value_score, momentum_score, quality_score, composite_score)
            returns: 종목별 실현 수익률 Series (index=ticker)

        Returns:
            {factor_name: contribution} dict
        """
        factor_cols = ["value_score", "momentum_score", "quality_score"]
        available = [c for c in factor_cols if c in composite_df.columns]
        if not available:
            return {}

        # 공통 종목
        common = composite_df.index.intersection(returns.index)
        if len(common) < 5:
            return {}

        result: dict[str, float] = {}
        for col in available:
            scores = composite_df.loc[common, col].dropna()
            rets = returns.reindex(scores.index).dropna()
            common_idx = scores.index.intersection(rets.index)
            if len(common_idx) < 5:
                result[col] = 0.0
                continue
            # 스코어-수익률 상관 (IC: Information Coefficient)
            corr = scores.loc[common_idx].corr(rets.loc[common_idx])
            result[col] = float(corr) if not np.isnan(corr) else 0.0

        logger.info(
            "팩터 IC: " + ", ".join(f"{k}={v:.3f}" for k, v in result.items())
        )
        return result

    def summary(
        self,
        portfolio_values: pd.Series,
        returns: pd.Series,
        risk_free: float = RF_ANNUAL,
        benchmark_values: pd.Series | None = None,
        benchmark_returns: pd.Series | None = None,
    ) -> dict:
        """전체 성과 지표 딕셔너리 반환

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series
            returns: 일별 수익률 Series
            risk_free: 연간 무위험 수익률
            benchmark_values: 벤치마크 가치 Series (선택)
            benchmark_returns: 벤치마크 수익률 Series (선택)

        Returns:
            dict {지표명: 값}
        """
        cagr = self.calculate_cagr(portfolio_values)
        mdd = self.calculate_mdd(portfolio_values)
        sharpe = self.calculate_sharpe(returns, risk_free)
        sortino = self.calculate_sortino(returns, risk_free)
        calmar = self.calculate_calmar(cagr, mdd)
        win_rate = self.calculate_win_rate(returns)
        volatility = self.calculate_volatility(returns)
        var_95 = self.calculate_var(returns, 0.95)
        mdd_recovery_days = self.calculate_mdd_recovery_days(portfolio_values)
        total_return = (
            float(portfolio_values.iloc[-1] / portfolio_values.iloc[0] - 1)
            if len(portfolio_values) >= 2
            else 0.0
        )
        n_years = len(returns) / 252

        metrics: dict = {
            "cagr": cagr,
            "total_return": total_return,
            "volatility": volatility,
            "mdd": mdd,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "var_95": var_95,
            "win_rate": win_rate,
            "mdd_recovery_days": mdd_recovery_days,
            "n_years": n_years,
        }

        # 벤치마크 지표 (선택)
        if benchmark_values is not None and len(benchmark_values) >= 2:
            bm_cagr = self.calculate_cagr(benchmark_values)
            bm_mdd = self.calculate_mdd(benchmark_values)
            metrics["benchmark_cagr"] = bm_cagr
            metrics["benchmark_mdd"] = bm_mdd
            metrics["excess_return"] = cagr - bm_cagr

        if benchmark_returns is not None and len(benchmark_returns) >= 10:
            metrics["information_ratio"] = self.calculate_information_ratio(
                returns, benchmark_returns
            )

        logger.info("=== 성과 지표 ===")
        logger.info(f"  CAGR:         {cagr * 100:.2f}%")
        logger.info(f"  총 수익률:     {total_return * 100:.2f}%")
        logger.info(f"  연환산 변동성: {volatility * 100:.2f}%")
        logger.info(f"  MDD:          {mdd * 100:.2f}%")
        logger.info(f"  샤프 비율:     {sharpe:.3f}")
        logger.info(f"  소르티노:      {sortino:.3f}")
        logger.info(f"  칼마 비율:     {calmar:.3f}")
        logger.info(f"  일 VaR(95%):  {var_95 * 100:.2f}%")
        logger.info(f"  일 승률:       {win_rate * 100:.1f}%")
        logger.info(f"  MDD 회복:      {mdd_recovery_days}거래일")
        logger.info(f"  투자 기간:     {n_years:.1f}년")
        if "excess_return" in metrics:
            logger.info(f"  초과수익률:    {metrics['excess_return'] * 100:.2f}%")
        if "information_ratio" in metrics:
            logger.info(f"  정보 비율:     {metrics['information_ratio']:.3f}")

        return metrics
