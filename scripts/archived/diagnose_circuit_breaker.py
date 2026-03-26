"""서킷브레이커 발동/재진입 추적 + 무작위 포트폴리오 기준선"""
import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd
import numpy as np

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


def diag_circuit_breaker() -> dict:
    """1. 서킷브레이커 추적"""
    print("=" * 75)
    print("진단 1: 서킷브레이커 발동/재진입 추적")
    print("=" * 75)

    settings.portfolio.rebalance_frequency = "quarterly"
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)

    # 로그 파일에서 서킷브레이커 이벤트 추출
    print("\n로그에서 서킷브레이커 이벤트를 추출합니다...")
    cb_events = []
    try:
        with open("logs/quant.log", "r", encoding="utf-8") as f:
            for line in f:
                if "서킷브레이커" in line and "2026-03-26" in line:
                    cb_events.append(line.strip())
    except Exception:
        pass

    if cb_events:
        print(f"\n서킷브레이커 이벤트 ({len(cb_events)}건):")
        for ev in cb_events:
            # 날짜와 핵심 내용만 추출
            parts = ev.split("]")
            if len(parts) >= 3:
                ts = parts[0].split("[")[-1] if "[" in parts[0] else ""
                msg = "]".join(parts[2:]).strip()
                print(f"  {ts}: {msg}")
            else:
                print(f"  {ev[-100:]}")
    else:
        print("  서킷브레이커 이벤트 없음 (로그에서 찾지 못함)")

    # 결과 DataFrame에서 MDD 추적
    pv = result["portfolio_value"]
    peak = pv.cummax()
    dd = (pv - peak) / peak

    # MDD 최저점
    mdd_date = dd.idxmin()
    mdd_val = dd.min()
    pv_at_mdd = pv.loc[mdd_date]
    peak_at_mdd = peak.loc[mdd_date]

    print(f"\nMDD 분석:")
    print(f"  MDD: {mdd_val:.2%}")
    print(f"  MDD 날짜: {mdd_date}")
    print(f"  MDD 시점 포트폴리오: {pv_at_mdd:,.0f}")
    print(f"  MDD 시점 고점: {peak_at_mdd:,.0f}")

    # DD가 -25% 이하인 구간 추적
    severe_dd = dd[dd < -0.25]
    if not severe_dd.empty:
        print(f"\n  DD < -25% 구간: {len(severe_dd)}거래일")
        print(f"  시작: {severe_dd.index[0]}")
        print(f"  종료: {severe_dd.index[-1]}")

    # 포트폴리오 가치 변화 핵심 구간
    print(f"\n포트폴리오 가치 추이:")
    for year in range(2017, 2025):
        yearly = pv[pv.index.year == year]
        if len(yearly) >= 2:
            start_v = yearly.iloc[0]
            end_v = yearly.iloc[-1]
            ret = end_v / start_v - 1
            yr_dd = dd[dd.index.year == year].min()
            print(f"  {year}: {start_v:>12,.0f} -> {end_v:>12,.0f} ({ret:>+6.1%}, DD={yr_dd:>+6.1%})")

    # n_holdings 추적 (현금 100% 구간 확인)
    if "n_holdings" in result.columns:
        zero_hold = result[result["n_holdings"] == 0]
        if not zero_hold.empty:
            print(f"\n  보유 종목 0개 (현금 100%) 구간: {len(zero_hold)}거래일")
            # 연속 구간 표시
            gaps = (zero_hold.index.to_series().diff() > pd.Timedelta(days=5))
            segments = gaps.cumsum()
            for seg_id in segments.unique():
                seg = zero_hold[segments == seg_id]
                print(f"    {seg.index[0].strftime('%Y-%m-%d')} ~ {seg.index[-1].strftime('%Y-%m-%d')} ({len(seg)}일)")

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(pv, returns)

    return metrics


def diag_random_portfolio() -> dict:
    """2. 무작위 포트폴리오 기준선"""
    print("\n" + "=" * 75)
    print("진단 2: 무작위 포트폴리오 (팩터 없이 유니버스 전체 동일 가중)")
    print("=" * 75)

    # screener의 유니버스 필터만 사용, 팩터 스코어링 없이
    # 유니버스 전체를 동일 가중 매수
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()

    # 팩터 가중치를 무시하고 유니버스 전체를 반환하도록 screener를 임시 오버라이드
    # composite_score를 랜덤으로 설정하여 종목 선정의 편향 제거
    original_screen = screener.screen

    def random_screen(date, market=None, n_stocks=None):
        """유니버스 필터만 적용, 팩터 스코어링 없이 전체 종목 반환"""
        # 넓은 후보를 가져오기 위해 큰 n_stocks 설정
        result = original_screen(date, market=market, n_stocks=500)
        if result.empty:
            return result
        # composite_score를 랜덤으로 덮어씌워 팩터 편향 제거
        np.random.seed(hash(date) % 2**31)
        result["composite_score"] = np.random.uniform(0, 100, len(result))
        result = result.sort_values("composite_score", ascending=False)
        # 상위 n_stocks개 선택 (무작위)
        n = n_stocks or settings.portfolio.n_stocks
        return result.head(n)

    screener.screen = random_screen

    settings.portfolio.rebalance_frequency = "quarterly"

    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    # screener 교체
    engine.screener = screener

    result = engine.run(START, END)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)

    return metrics


def get_kospi() -> dict:
    collector = KRXDataCollector()
    df = collector.get_ohlcv(KODEX200, START.replace("-", ""), END.replace("-", ""))
    if df is None or df.empty:
        return {"cagr": 0, "mdd": 0, "total_return": 0}
    close = df["close"]
    close.index = pd.to_datetime(close.index)
    total = close.iloc[-1] / close.iloc[0] - 1
    cagr = (1 + total) ** (1/8) - 1
    peak = close.cummax()
    mdd = ((close - peak) / peak).min()
    return {"cagr": cagr, "mdd": mdd, "total_return": total}


def main():
    # 1. 서킷브레이커 추적
    strat_metrics = diag_circuit_breaker()

    # 2. 무작위 포트폴리오
    rand_metrics = diag_random_portfolio()

    # 3. KOSPI
    kospi = get_kospi()

    # 비교 테이블
    print("\n" + "=" * 75)
    print(f"{'전략 vs 무작위 vs KOSPI 비교':^75}")
    print("=" * 75)
    print(f"{'지표':>14} | {'KOSPI':>12} | {'무작위':>12} | {'전략(A)':>12}")
    print("-" * 60)
    for key, label in [("cagr", "CAGR"), ("mdd", "MDD"), ("total_return", "Total Return")]:
        k = kospi.get(key, 0)
        r = rand_metrics.get(key, 0)
        s = strat_metrics.get(key, 0)
        print(f"{label:>14} | {k:>11.2%} | {r:>11.2%} | {s:>11.2%}")

    sharpe_r = rand_metrics.get("sharpe", 0)
    sharpe_s = strat_metrics.get("sharpe", 0)
    print(f"{'Sharpe':>14} | {'—':>12} | {sharpe_r:>11.3f} | {sharpe_s:>11.3f}")
    print("=" * 75)


if __name__ == "__main__":
    main()
