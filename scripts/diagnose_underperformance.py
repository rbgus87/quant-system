"""백테스트 언더퍼폼 근본 원인 진단

5가지 진단:
1. KOSPI 벤치마크 비교 (연도별)
2. 종목 선정 품질 검증
3. 동일 가중 유니버스 기준선
4. 거래 비용 제거 시뮬레이션
5. 리밸런싱 로그 샘플 (2023)
"""
import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd
import numpy as np
from datetime import datetime

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener
from data.collector import KRXDataCollector

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"
KODEX200 = "069500"


def diag1_benchmark() -> None:
    """1. KOSPI 벤치마크 비교 (연도별)"""
    print("\n" + "=" * 70)
    print("진단 1: KOSPI(KODEX 200) vs 전략 연도별 수익률")
    print("=" * 70)

    # KODEX 200 Buy & Hold
    collector = KRXDataCollector()
    df = collector.get_ohlcv(KODEX200, START.replace("-", ""), END.replace("-", ""))
    if df is None or df.empty:
        print("KODEX 200 데이터 없음")
        return

    bm_close = df["close"]
    bm_close.index = pd.to_datetime(bm_close.index)

    # 전략 백테스트
    settings.portfolio.rebalance_frequency = "quarterly"
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)
    port_val = result["portfolio_value"]

    # 연도별 수익률
    print(f"\n{'연도':>6} | {'KOSPI':>8} | {'전략':>8} | {'초과':>8} | 판정")
    print("-" * 55)

    for year in range(2017, 2025):
        # KOSPI
        bm_year = bm_close[bm_close.index.year == year]
        if len(bm_year) < 2:
            continue
        bm_ret = bm_year.iloc[-1] / bm_year.iloc[0] - 1

        # 전략
        pv_year = port_val[port_val.index.year == year]
        if len(pv_year) < 2:
            strat_ret = 0
        else:
            strat_ret = pv_year.iloc[-1] / pv_year.iloc[0] - 1

        excess = strat_ret - bm_ret
        verdict = "+" if excess > 0 else "---" if excess < -0.10 else "-"
        print(f"{year:>6} | {bm_ret:>7.1%} | {strat_ret:>7.1%} | {excess:>+7.1%} | {verdict}")

    # 전체
    bm_total = bm_close.iloc[-1] / bm_close.iloc[0] - 1
    strat_total = port_val.iloc[-1] / port_val.iloc[0] - 1
    print("-" * 55)
    print(f"{'전체':>6} | {bm_total:>7.1%} | {strat_total:>7.1%} | {strat_total - bm_total:>+7.1%}")
    print()

    return result  # 다른 진단에서 재사용


def diag2_stock_quality() -> None:
    """2. 종목 선정 품질 검증 (최근 리밸런싱)"""
    print("\n" + "=" * 70)
    print("진단 2: 최근 종목 선정 품질 (2024-09-30 기준)")
    print("=" * 70)

    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()
    # 분기 리밸런싱이므로 9월 마지막 거래일
    portfolio_df = screener.screen("20240930", n_stocks=20)

    if portfolio_df.empty:
        print("스크리닝 결과 없음")
        return

    collector = screener.collector
    selected = portfolio_df.index.tolist()

    print(f"\n{'순위':>4} | {'종목코드':>8} | {'종목명':>12} | {'복합점수':>8} | {'1M수익률':>8} | {'3M수익률':>8}")
    print("-" * 70)

    rets_1m = []
    rets_3m = []
    for i, ticker in enumerate(selected):
        name = collector.get_ticker_name(ticker) or ticker
        score = portfolio_df.loc[ticker, "composite_score"]

        # 이후 수익률: 10/1 시가 대비
        try:
            ohlcv = collector.get_ohlcv(ticker, "20241001", "20250101")
            if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 2:
                start_p = ohlcv["close"].iloc[0]
                # 1개월 (약 21영업일)
                end_1m = ohlcv["close"].iloc[min(20, len(ohlcv) - 1)]
                ret_1m = end_1m / start_p - 1
                # 3개월 (약 63영업일)
                end_3m = ohlcv["close"].iloc[min(62, len(ohlcv) - 1)]
                ret_3m = end_3m / start_p - 1
            else:
                ret_1m = ret_3m = float("nan")
        except Exception:
            ret_1m = ret_3m = float("nan")

        rets_1m.append(ret_1m)
        rets_3m.append(ret_3m)
        r1 = f"{ret_1m:>7.1%}" if not np.isnan(ret_1m) else "   N/A"
        r3 = f"{ret_3m:>7.1%}" if not np.isnan(ret_3m) else "   N/A"
        print(f"{i+1:>4} | {ticker:>8} | {name:>12} | {score:>8.1f} | {r1} | {r3}")

    valid_1m = [r for r in rets_1m if not np.isnan(r)]
    valid_3m = [r for r in rets_3m if not np.isnan(r)]
    if valid_1m:
        print("-" * 70)
        print(f"     평균: 1M={np.mean(valid_1m):>+.1%}, 3M={np.mean(valid_3m):>+.1%}")
        print(f"     승률: 1M={sum(1 for r in valid_1m if r > 0)/len(valid_1m):.0%}, "
              f"3M={sum(1 for r in valid_3m if r > 0)/len(valid_3m):.0%}")
    print()


def diag4_zero_cost() -> None:
    """4. 거래 비용 제거 시뮬레이션"""
    print("\n" + "=" * 70)
    print("진단 4: 거래 비용 제거 시뮬레이션")
    print("=" * 70)

    # 비용 백업
    orig_comm = settings.trading.commission_rate
    orig_tax = settings.trading.tax_rate
    orig_slip = settings.trading.slippage

    results = {}
    for label, comm, tax, slip in [
        ("비용 있음", orig_comm, orig_tax, orig_slip),
        ("비용 없음", 0.0, 0.0, 0.0),
    ]:
        settings.trading.commission_rate = comm
        settings.trading.tax_rate = tax
        settings.trading.slippage = slip
        settings.portfolio.rebalance_frequency = "quarterly"

        MultiFactorScreener._factor_cache.clear()
        engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
        result = engine.run(START, END)

        analyzer = PerformanceAnalyzer()
        returns = result["returns"].dropna()
        metrics = analyzer.summary(result["portfolio_value"], returns)
        results[label] = metrics

    # 원복
    settings.trading.commission_rate = orig_comm
    settings.trading.tax_rate = orig_tax
    settings.trading.slippage = orig_slip

    print(f"\n{'지표':>18} | {'비용 있음':>12} | {'비용 없음':>12} | {'비용 영향':>10}")
    print("-" * 60)
    for key, name in [("cagr","CAGR"), ("mdd","MDD"), ("sharpe","Sharpe"), ("total_return","Total Return")]:
        a = results["비용 있음"].get(key, 0)
        b = results["비용 없음"].get(key, 0)
        diff = b - a
        if key in ("cagr", "mdd", "total_return"):
            print(f"{name:>18} | {a:>11.2%} | {b:>11.2%} | {diff:>+9.2%}")
        else:
            print(f"{name:>18} | {a:>11.3f} | {b:>11.3f} | {diff:>+9.3f}")
    print()


def diag5_rebal_log() -> None:
    """5. 리밸런싱 로그 샘플 (2023)"""
    print("\n" + "=" * 70)
    print("진단 5: 2023년 리밸런싱 로그")
    print("=" * 70)

    settings.portfolio.rebalance_frequency = "quarterly"
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run("2022-01-01", "2023-12-31")

    turnover_log = result.attrs.get("turnover_log", [])
    logs_2023 = [t for t in turnover_log if t.get("date", "").startswith("2023")]

    if not logs_2023:
        print("2023년 리밸런싱 로그 없음")
        return

    print(f"\n{'날짜':>10} | {'매도':>4} | {'매수':>4} | {'턴오버':>7} | {'보유전':>6} | {'보유후':>6}")
    print("-" * 55)
    for t in logs_2023:
        print(
            f"{t['date']:>10} | {t['sells']:>4} | {t['buys']:>4} | "
            f"{t['turnover_rate']:>6.0%} | {t['n_holdings_before']:>6} | "
            f"{t['n_holdings_after']:>6}"
        )

    avg_to = np.mean([t["turnover_rate"] for t in logs_2023])
    avg_sells = np.mean([t["sells"] for t in logs_2023])
    print("-" * 55)
    print(f"  평균: 매도 {avg_sells:.0f}개/회, 턴오버 {avg_to:.0%}")

    # 반복 매수매도 패턴 감지
    all_sells = set()
    all_buys = set()
    for t in logs_2023:
        for d in t.get("sell_details", []):
            if isinstance(d, dict):
                all_sells.add(d.get("ticker", ""))
        for d in t.get("buy_details", []):
            if isinstance(d, dict):
                all_buys.add(d.get("ticker", ""))
    churned = all_sells & all_buys
    if churned:
        print(f"\n  반복 매수매도 종목: {len(churned)}개 ({', '.join(list(churned)[:5])}...)")
    print()


if __name__ == "__main__":
    print("=" * 70)
    print("백테스트 언더퍼폼 근본 원인 진단")
    print(f"기간: {START} ~ {END}")
    print("=" * 70)

    # 진단 4 먼저 (가장 중요 — 팩터 자체가 역효과인지)
    diag4_zero_cost()

    # 진단 1: 벤치마크 비교
    diag1_benchmark()

    # 진단 2: 종목 선정 품질
    diag2_stock_quality()

    # 진단 5: 리밸런싱 로그
    diag5_rebal_log()
