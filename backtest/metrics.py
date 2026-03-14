# backtest/metrics.py
import pandas as pd
import numpy as np
import logging

from config.settings import settings

logger = logging.getLogger(__name__)

# 무위험 수익률: config/settings.py의 momentum.risk_free_rate를 단일 소스로 사용
RF_ANNUAL: float = settings.momentum.risk_free_rate


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

    def top_drawdowns(
        self, portfolio_values: pd.Series, n: int = 5
    ) -> list[dict]:
        """Top N 낙폭 구간 상세 정보

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series
            n: 상위 N개

        Returns:
            [{start, trough, end, days, recovery_days, depth}, ...]
        """
        if len(portfolio_values) < 2:
            return []

        rolling_max = portfolio_values.cummax()
        drawdown = (portfolio_values - rolling_max) / rolling_max

        # 낙폭 구간 식별: drawdown < 0 인 연속 구간
        is_dd = drawdown < -1e-8
        segments: list[dict] = []
        in_dd = False
        start_idx = 0

        for i in range(len(drawdown)):
            if is_dd.iloc[i] and not in_dd:
                in_dd = True
                start_idx = max(0, i - 1)  # 고점
            elif not is_dd.iloc[i] and in_dd:
                in_dd = False
                seg = drawdown.iloc[start_idx:i + 1]
                trough_idx = seg.idxmin()
                segments.append({
                    "start": portfolio_values.index[start_idx],
                    "trough": trough_idx,
                    "end": portfolio_values.index[i],
                    "depth": float(seg.min()),
                    "days": (trough_idx - portfolio_values.index[start_idx]).days,
                    "recovery_days": (portfolio_values.index[i] - trough_idx).days,
                })

        # 마지막 미회복 구간
        if in_dd:
            seg = drawdown.iloc[start_idx:]
            trough_idx = seg.idxmin()
            segments.append({
                "start": portfolio_values.index[start_idx],
                "trough": trough_idx,
                "end": None,  # 미회복
                "depth": float(seg.min()),
                "days": (trough_idx - portfolio_values.index[start_idx]).days,
                "recovery_days": None,
            })

        # 깊이 기준 정렬
        segments.sort(key=lambda x: x["depth"])
        return segments[:n]

    def rolling_returns(
        self, portfolio_values: pd.Series, window: int = 252
    ) -> pd.Series:
        """롤링 수익률

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series
            window: 롤링 윈도우 (거래일 기준, 기본 252 = 12개월)

        Returns:
            롤링 수익률 Series
        """
        if len(portfolio_values) < window:
            return pd.Series(dtype=float)
        return portfolio_values.pct_change(window).dropna()

    def rolling_sharpe(
        self, returns: pd.Series, window: int = 252, risk_free: float = RF_ANNUAL
    ) -> pd.Series:
        """롤링 샤프 비율

        Args:
            returns: 일별 수익률 Series
            window: 롤링 윈도우 (거래일)
            risk_free: 연간 무위험 수익률

        Returns:
            롤링 샤프 Series
        """
        if len(returns) < window:
            return pd.Series(dtype=float)

        rf_daily = risk_free / 252
        excess = returns - rf_daily
        roll_mean = excess.rolling(window).mean()
        roll_std = excess.rolling(window).std()
        # std가 0에 가까운 경우 방지
        roll_std = roll_std.replace(0, np.nan)
        return (roll_mean / roll_std * np.sqrt(252)).dropna()

    def return_distribution(self, returns: pd.Series) -> dict:
        """수익률 분포 통계

        Args:
            returns: 일별 수익률 Series

        Returns:
            {skewness, kurtosis, max_consecutive_loss, max_consecutive_win}
        """
        if len(returns) < 10:
            return {
                "skewness": 0.0,
                "kurtosis": 0.0,
                "max_consecutive_loss": 0,
                "max_consecutive_win": 0,
            }

        skew = float(returns.skew())
        kurt = float(returns.kurtosis())

        # 최대 연속 손실/수익
        def _max_consecutive(series: pd.Series, positive: bool) -> int:
            mask = series > 0 if positive else series < 0
            max_count = 0
            count = 0
            for v in mask:
                if v:
                    count += 1
                    max_count = max(max_count, count)
                else:
                    count = 0
            return max_count

        return {
            "skewness": skew,
            "kurtosis": kurt,
            "max_consecutive_loss": _max_consecutive(returns, positive=False),
            "max_consecutive_win": _max_consecutive(returns, positive=True),
        }

    def best_worst_periods(
        self, portfolio_values: pd.Series, returns: pd.Series
    ) -> dict:
        """최고/최저 기간 수익률

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series
            returns: 일별 수익률 Series

        Returns:
            {best_day, worst_day, best_month, worst_month, best_year, worst_year}
            각 항목은 {date, value} dict
        """
        result: dict = {}

        # 일별
        if len(returns) > 0:
            best_idx = returns.idxmax()
            worst_idx = returns.idxmin()
            result["best_day"] = {"date": best_idx, "value": float(returns.loc[best_idx])}
            result["worst_day"] = {"date": worst_idx, "value": float(returns.loc[worst_idx])}

        # 월별
        if len(portfolio_values) > 20:
            monthly = portfolio_values.resample("ME").last().pct_change().dropna()
            if len(monthly) > 0:
                best_m = monthly.idxmax()
                worst_m = monthly.idxmin()
                result["best_month"] = {"date": best_m, "value": float(monthly.loc[best_m])}
                result["worst_month"] = {"date": worst_m, "value": float(monthly.loc[worst_m])}

        # 연별
        yearly = self.yearly_returns(portfolio_values)
        if len(yearly) > 0:
            best_y = yearly.idxmax()
            worst_y = yearly.idxmin()
            result["best_year"] = {"date": best_y, "value": float(yearly.loc[best_y])}
            result["worst_year"] = {"date": worst_y, "value": float(yearly.loc[worst_y])}

        return result

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

    def monthly_pnl(self, portfolio_values: pd.Series) -> pd.DataFrame:
        """월별 손익 상세 (시작금액, 종료금액, 손익금액, 수익률)

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series (index=date)

        Returns:
            DataFrame (columns: year, month, start_value, end_value, pnl, return_pct)
        """
        if len(portfolio_values) < 2:
            return pd.DataFrame()

        monthly_last = portfolio_values.resample("ME").last()
        monthly_first = portfolio_values.resample("MS").first()

        records: list[dict] = []
        for i in range(len(monthly_last)):
            end_val = monthly_last.iloc[i]
            dt = monthly_last.index[i]

            # 시작값: 해당 월 첫 거래일 값
            month_mask = (portfolio_values.index.year == dt.year) & (
                portfolio_values.index.month == dt.month
            )
            month_data = portfolio_values[month_mask]
            if len(month_data) == 0:
                continue
            start_val = month_data.iloc[0]

            pnl = end_val - start_val
            ret = pnl / start_val if start_val != 0 else 0.0

            records.append({
                "year": dt.year,
                "month": dt.month,
                "start_value": float(start_val),
                "end_value": float(end_val),
                "pnl": float(pnl),
                "return_pct": float(ret),
            })

        return pd.DataFrame(records)

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
