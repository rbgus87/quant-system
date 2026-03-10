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

    def summary(
        self,
        portfolio_values: pd.Series,
        returns: pd.Series,
        risk_free: float = RF_ANNUAL,
    ) -> dict:
        """전체 성과 지표 딕셔너리 반환

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series
            returns: 일별 수익률 Series
            risk_free: 연간 무위험 수익률

        Returns:
            dict {지표명: 값}
        """
        cagr = self.calculate_cagr(portfolio_values)
        mdd = self.calculate_mdd(portfolio_values)
        sharpe = self.calculate_sharpe(returns, risk_free)
        calmar = self.calculate_calmar(cagr, mdd)
        win_rate = self.calculate_win_rate(returns)
        volatility = self.calculate_volatility(returns)
        total_return = (
            float(portfolio_values.iloc[-1] / portfolio_values.iloc[0] - 1)
            if len(portfolio_values) >= 2
            else 0.0
        )
        n_years = len(returns) / 252

        metrics = {
            "cagr": cagr,
            "total_return": total_return,
            "volatility": volatility,
            "mdd": mdd,
            "sharpe": sharpe,
            "calmar": calmar,
            "win_rate": win_rate,
            "n_years": n_years,
        }

        logger.info("=== 성과 지표 ===")
        logger.info(f"  CAGR:         {cagr * 100:.2f}%")
        logger.info(f"  총 수익률:     {total_return * 100:.2f}%")
        logger.info(f"  연환산 변동성: {volatility * 100:.2f}%")
        logger.info(f"  MDD:          {mdd * 100:.2f}%")
        logger.info(f"  샤프 비율:     {sharpe:.3f}")
        logger.info(f"  칼마 비율:     {calmar:.3f}")
        logger.info(f"  일 승률:       {win_rate * 100:.1f}%")
        logger.info(f"  투자 기간:     {n_years:.1f}년")

        return metrics
