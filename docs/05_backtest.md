# 05. 백테스트 엔진

## 5-1. 설계 원칙

| 원칙 | 구현 방법 |
|------|----------|
| 생존 편향 방지 | 리밸런싱 날짜 기준으로 pykrx 종목 조회 (상장폐지 종목 자동 포함) |
| 선견 편향 방지 | 월말(T) 신호 계산 → T+1 영업일 시가(open)로 매매 체결 |
| 거래 비용 반영 | 매수: 수수료 + 슬리피지 / 매도: 수수료 + 세금 + 슬리피지 |
| In/Out 분리 | In-Sample: 2015~2020 / Out-of-Sample: 2021~2024 |

---

## 5-2. backtest/engine.py

```python
# backtest/engine.py
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from typing import Optional
from data.collector import KRXDataCollector, ReturnCalculator
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite
from config.settings import settings

logger = logging.getLogger(__name__)


class MultiFactorBacktest:
    """
    멀티팩터 전략 월별 리밸런싱 백테스트 엔진

    흐름:
      리밸런싱일(T, 월 마지막 영업일) → 팩터 계산 → 포트폴리오 결정
      → T+1 영업일 시가(open)로 매매 체결 (선견 편향 방지)
    """

    def __init__(self, initial_cash: float = 10_000_000):
        self.initial_cash = initial_cash
        self.krx = KRXDataCollector()
        self.ret_calc = ReturnCalculator()
        self.value_f = ValueFactor()
        self.momentum_f = MomentumFactor()
        self.quality_f = QualityFactor()
        self.composite = MultiFactorComposite()

    def run(
        self,
        start_date: str,
        end_date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """
        백테스트 실행

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date:   종료일 (YYYY-MM-DD)
            market:     대상 시장

        Returns:
            DataFrame(index=date, columns=[portfolio_value, cash, n_holdings, returns])
        """
        logger.info(f"백테스트 시작: {start_date} ~ {end_date}")

        # 리밸런싱 날짜 목록 (매월 마지막 영업일)
        rebal_dates = self._get_rebalance_dates(start_date, end_date)
        logger.info(f"리밸런싱 횟수: {len(rebal_dates)}회")

        portfolio = {
            "cash": self.initial_cash,
            "holdings": {},  # {ticker: shares}
            "history": [],
        }
        current_portfolio: list[str] = []

        for i, rebal_dt in enumerate(rebal_dates):
            date_str = rebal_dt.strftime("%Y%m%d")
            logger.info(f"[{i+1}/{len(rebal_dates)}] 리밸런싱 신호 계산: {date_str}")

            try:
                # ① T일 기준 팩터 계산 → 포트폴리오 결정
                new_portfolio = self._calc_portfolio(date_str, market)
                if not new_portfolio:
                    logger.warning(f"{date_str}: 포트폴리오 계산 실패, 스킵")
                    continue

                # ② T+1 영업일 시가로 체결 (선견 편향 방지)
                next_dt = self._next_business_day(rebal_dt)
                self._execute_rebalancing(
                    portfolio, current_portfolio, new_portfolio, next_dt
                )
                current_portfolio = new_portfolio

                # ③ 다음 리밸런싱까지 일별 포트폴리오 가치 기록
                period_end = (
                    rebal_dates[i + 1] if i + 1 < len(rebal_dates)
                    else pd.Timestamp(end_date)
                )
                self._record_period(portfolio, current_portfolio, next_dt, period_end)

            except Exception as e:
                logger.error(f"리밸런싱 실패 ({date_str}): {e}", exc_info=True)
                continue

        # 결과 정리
        if not portfolio["history"]:
            raise ValueError("백테스트 결과 없음. 날짜 범위와 데이터를 확인하세요.")

        result = pd.DataFrame(portfolio["history"]).set_index("date")
        result["returns"] = result["portfolio_value"].pct_change()

        total_ret = result["portfolio_value"].iloc[-1] / self.initial_cash - 1
        logger.info(f"백테스트 완료 | 총 수익률: {total_ret*100:.2f}%")
        return result

    # ─────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────

    def _calc_portfolio(self, date_str: str, market: str) -> list[str]:
        """T일 기준 팩터 계산 후 상위 30개 종목 반환"""
        fundamentals = self.krx.get_fundamentals_all(date_str, market)
        if fundamentals.empty:
            return []

        market_cap_df = self.krx.get_market_cap(date_str, market)
        market_cap = market_cap_df["market_cap"] if "market_cap" in market_cap_df.columns else pd.Series(dtype=float)

        tickers = fundamentals.index.tolist()

        # 팩터 계산
        value_score = self.value_f.calculate(fundamentals)
        returns_12m = self.ret_calc.get_returns_for_universe(tickers, date_str, 12, 1)
        returns_3m  = self.ret_calc.get_returns_for_universe(tickers, date_str, 3, 1)
        momentum_score = self.momentum_f.calculate(returns_12m, returns_3m=returns_3m)
        quality_score = self.quality_f.calculate(fundamentals)

        # 합산 → 필터 → 선정
        composite_df = self.composite.calculate(value_score, momentum_score, quality_score)
        filtered_df  = self.composite.apply_universe_filter(composite_df, market_cap)
        selected     = self.composite.select_top(filtered_df)

        return selected.index.tolist()

    def _execute_rebalancing(
        self,
        portfolio: dict,
        old_portfolio: list[str],
        new_portfolio: list[str],
        trade_dt: pd.Timestamp,
    ):
        """T+1 영업일 시가 기준 리밸런싱 실행 (거래 비용 포함)"""
        date_str = trade_dt.strftime("%Y%m%d")
        cfg = settings.trading

        sell_list = [t for t in old_portfolio if t not in new_portfolio]
        buy_list  = [t for t in new_portfolio if t not in old_portfolio]

        # 1단계: 매도 (수수료 + 세금)
        for ticker in sell_list:
            shares = portfolio["holdings"].pop(ticker, 0)
            if shares <= 0:
                continue
            price = self._get_open_price(ticker, date_str)
            if price is None:
                continue
            proceed = price * shares
            cost = proceed * (cfg.commission_rate + cfg.tax_rate + cfg.slippage)
            portfolio["cash"] += proceed - cost

        # 2단계: 총 자산 평가 후 목표 비중 계산
        total_value = portfolio["cash"]
        for ticker, shares in portfolio["holdings"].items():
            price = self._get_open_price(ticker, date_str)
            if price:
                total_value += price * shares

        if len(new_portfolio) == 0:
            return

        target_per_stock = total_value / len(new_portfolio)

        # 3단계: 매수 (수수료 + 슬리피지)
        for ticker in buy_list:
            price = self._get_open_price(ticker, date_str)
            if price is None:
                continue
            # 슬리피지: 시가보다 약간 높게 체결된다고 가정
            exec_price = price * (1 + cfg.slippage)
            cost_per_share = exec_price * (1 + cfg.commission_rate)
            shares = int(target_per_stock / cost_per_share)
            if shares <= 0:
                continue
            total_cost = cost_per_share * shares
            if portfolio["cash"] >= total_cost:
                portfolio["cash"] -= total_cost
                portfolio["holdings"][ticker] = shares

    def _record_period(
        self,
        portfolio: dict,
        tickers: list[str],
        start: pd.Timestamp,
        end: pd.Timestamp,
    ):
        """기간 내 일별 포트폴리오 가치 기록"""
        # 영업일만 (B = Business Day)
        dates = pd.bdate_range(start, end)
        for dt in dates:
            date_str = dt.strftime("%Y%m%d")
            total = portfolio["cash"]
            for ticker in tickers:
                shares = portfolio["holdings"].get(ticker, 0)
                if shares <= 0:
                    continue
                price = self._get_open_price(ticker, date_str)
                if price:
                    total += price * shares

            portfolio["history"].append({
                "date": dt,
                "portfolio_value": total,
                "cash": portfolio["cash"],
                "n_holdings": len(portfolio["holdings"]),
            })

    def _get_open_price(self, ticker: str, date_str: str) -> Optional[float]:
        """특정 날짜 시가 조회 (없으면 None)"""
        df = self.krx.get_ohlcv(ticker, date_str, date_str)
        if df is not None and not df.empty and "open" in df.columns:
            val = df["open"].iloc[0]
            return float(val) if val > 0 else None
        return None

    def _get_rebalance_dates(self, start_date: str, end_date: str) -> list[pd.Timestamp]:
        """
        매월 마지막 영업일 목록 생성

        ⚠️ pandas 2.2+에서 freq="BME" deprecated
           → pd.offsets.BMonthEnd() 사용
        """
        start = pd.Timestamp(start_date)
        end   = pd.Timestamp(end_date)
        dates = pd.date_range(start, end, freq=pd.offsets.BMonthEnd())
        return list(dates)

    def _next_business_day(self, dt: pd.Timestamp) -> pd.Timestamp:
        """다음 영업일 반환"""
        next_dt = dt + pd.offsets.BDay(1)
        return next_dt
```

---

## 5-3. backtest/metrics.py

```python
# backtest/metrics.py
import pandas as pd
import numpy as np
import quantstats as qs
import matplotlib.pyplot as plt
import logging
import os

logger = logging.getLogger(__name__)

RF_ANNUAL = 0.03   # 무위험 수익률 연 3% (한국 국고채 기준)


class PerformanceAnalyzer:
    """백테스트 성과 분석"""

    def calculate_metrics(self, result_df: pd.DataFrame) -> dict:
        """
        핵심 성과 지표 계산

        Args:
            result_df: engine.run() 반환값
                       (index=date, columns=[portfolio_value, returns, ...])

        Returns:
            dict {지표명: 값}
        """
        returns = result_df["returns"].dropna()
        pv = result_df["portfolio_value"]

        n_years = len(returns) / 252

        # CAGR
        total_ret = pv.iloc[-1] / pv.iloc[0] - 1
        cagr = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0

        # MDD
        cumret = (1 + returns).cumprod()
        rolling_max = cumret.cummax()
        drawdown = (cumret - rolling_max) / rolling_max
        mdd = float(drawdown.min())

        # 샤프 비율 (일별 무위험 수익률 차감)
        rf_daily = RF_ANNUAL / 252
        excess = returns - rf_daily
        sharpe = float((excess.mean() / excess.std()) * np.sqrt(252)) if excess.std() > 0 else 0

        # 칼마 비율
        calmar = cagr / abs(mdd) if mdd != 0 else 0

        # 일 승률
        win_rate = float((returns > 0).mean())

        # 연환산 변동성
        annual_vol = float(returns.std() * np.sqrt(252))

        metrics = {
            "CAGR":           f"{cagr*100:.2f}%",
            "총 수익률":       f"{total_ret*100:.2f}%",
            "연환산 변동성":   f"{annual_vol*100:.2f}%",
            "MDD":            f"{mdd*100:.2f}%",
            "샤프 비율":       f"{sharpe:.3f}",
            "칼마 비율":       f"{calmar:.3f}",
            "일 승률":        f"{win_rate*100:.1f}%",
            "투자 기간(년)":   f"{n_years:.1f}년",
        }

        for k, v in metrics.items():
            logger.info(f"  {k}: {v}")

        return metrics

    def generate_html_report(
        self,
        result_df: pd.DataFrame,
        output_path: str = "reports/backtest_report.html",
        title: str = "멀티팩터 퀀트 전략",
    ):
        """
        quantstats HTML 리포트 생성

        ⚠️ quantstats 벤치마크: FinanceDataReader로 KOSPI 수익률 전달
        """
        import FinanceDataReader as fdr

        returns = result_df["returns"].dropna()
        start = result_df.index[0].strftime("%Y-%m-%d")
        end   = result_df.index[-1].strftime("%Y-%m-%d")

        # KOSPI 벤치마크
        try:
            kospi = fdr.DataReader("KS11", start, end)["Close"].pct_change().dropna()
        except Exception as e:
            logger.warning(f"벤치마크 로드 실패: {e}. 벤치마크 없이 생성.")
            kospi = None

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        qs.reports.html(
            returns,
            benchmark=kospi,
            title=title,
            output=output_path,
        )
        logger.info(f"HTML 리포트 저장: {output_path}")

    def plot_performance(self, result_df: pd.DataFrame, save_path: str = "reports/performance.png"):
        """수익 곡선 + MDD 차트 저장"""
        returns = result_df["returns"].dropna()
        cumret = (1 + returns).cumprod() * 100
        rolling_max = cumret.cummax()
        drawdown = (cumret - rolling_max) / rolling_max * 100

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        ax1.plot(cumret.index, cumret.values, color="#2E75B6", linewidth=2)
        ax1.axhline(100, color="gray", linestyle="--", alpha=0.5)
        ax1.set_title("누적 수익률", fontsize=13)
        ax1.set_ylabel("수익률 (%)")
        ax1.grid(True, alpha=0.3)

        ax2.fill_between(drawdown.index, drawdown.values, 0, color="#E74C3C", alpha=0.5)
        ax2.set_title("드로다운", fontsize=13)
        ax2.set_ylabel("드로다운 (%)")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        logger.info(f"차트 저장: {save_path}")
```

---

## 5-4. 백테스트 실행 스크립트

```python
# run_backtest.py (프로젝트 루트에서 실행)
from config.logging_config import setup_logging
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer

setup_logging()

# In-Sample 백테스트
engine = MultiFactorBacktest(initial_cash=10_000_000)
result = engine.run(start_date="2015-01-01", end_date="2020-12-31")

analyzer = PerformanceAnalyzer()
metrics = analyzer.calculate_metrics(result)
print("\n=== In-Sample 성과 ===")
for k, v in metrics.items():
    print(f"  {k}: {v}")

analyzer.generate_html_report(result, "reports/insample_report.html", "멀티팩터 퀀트 (In-Sample)")
analyzer.plot_performance(result, "reports/insample_performance.png")

# Out-of-Sample 백테스트 (파라미터 고정 후)
result_oos = engine.run(start_date="2021-01-01", end_date="2024-12-31")
print("\n=== Out-of-Sample 성과 ===")
for k, v in analyzer.calculate_metrics(result_oos).items():
    print(f"  {k}: {v}")
```

---

## 5-5. 성과 목표 기준

| 지표 | 목표 | Pass 기준 | Fail 기준 |
|------|------|----------|----------|
| CAGR | 15%+ | 10%+ | 5% 미만 |
| MDD | -20% 이내 | -30% 이내 | -40% 초과 |
| 샤프 비율 | 1.0+ | 0.7+ | 0.5 미만 |
| 칼마 비율 | 0.5+ | 0.3+ | 0.2 미만 |
| KOSPI 대비 | 연 5%+ 초과 | 연 3%+ 초과 | 벤치마크 미달 |

---

## 5-6. 주의사항 (버그 방지)

| 항목 | 올바른 처리 |
|------|-----------|
| `freq="BME"` | pandas 2.2+ 에서 deprecated → `pd.offsets.BMonthEnd()` 사용 |
| 시가 체결 | `_get_open_price()`가 open 컬럼 없으면 None 반환 → 해당 종목 skip |
| pykrx 과호출 | `_calc_portfolio` 내 종목별 수익률 계산 시 delay 포함됨 (ReturnCalculator) |
| 백테스트 속도 | 종목당 OHLCV 조회가 느림 → 최초 1회 전체 기간 데이터 일괄 저장 후 로컬 DB 활용 권장 |
| 결과 저장 | `reports/` 디렉토리 자동 생성 처리 (`os.makedirs(..., exist_ok=True)`) |
