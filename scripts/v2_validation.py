"""v2.0 최종 전략 검증 — 깨끗한 상태에서 재검증

실험 1: 벤치마크 확립 (무작위 + KOSPI)
실험 2: 팩터별 단독 전략 (V100%, M100%, Q100%)
실험 3: 팩터 조합 전략 (프리셋A, V60M40, V70M30, V50M50)

공통 조건:
  - 기간: 2017-01-01 ~ 2024-12-31
  - 분기 리밸런싱, 동일가중, 20종목
  - CB OFF (max_drawdown_pct: null, trailing_stop_pct: 0)
  - 시장 레짐 유지
"""
import os
import sys
import logging
import copy
from dataclasses import fields as dc_fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import numpy as np
import pandas as pd

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"
N_STOCKS = 20


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def base_config() -> None:
    """공통 기본 설정: CB OFF, 분기, 동일가중, 20종목"""
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.portfolio.weight_method = "equal"
    settings.portfolio.n_stocks = N_STOCKS
    # 시장 레짐은 유지 (settings 기본값)


def run_bt(label: str, show_top: int = 5) -> dict:
    """백테스트 실행 + 성과 분석 + 상위 종목 출력"""
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["label"] = label

    # 연도별 수익률
    pv = result["portfolio_value"]
    yearly = {}
    for year in range(2017, 2025):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly

    # 상위 종목 출력 (첫 번째 리밸런싱 시점)
    if show_top > 0:
        screener = MultiFactorScreener()
        # 첫 분기말 조회
        test_dates = ["20170331", "20170630"]
        for td in test_dates:
            try:
                top_df = screener.screen(td, n_stocks=show_top)
                if not top_df.empty:
                    tickers = top_df.index.tolist()[:show_top]
                    scores = top_df["composite_score"].head(show_top).tolist()
                    print(f"  [{label}] {td} 상위{show_top}: "
                          f"{', '.join(f'{t}({s:.1f})' for t, s in zip(tickers, scores))}")
                    metrics["top5_tickers"] = tickers
                    break
            except Exception:
                continue

    return metrics


def run_random(label: str) -> dict:
    """무작위 포트폴리오 백테스트"""
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()
    orig = screener.screen

    def rand_screen(date: str, market: str | None = None,
                    n_stocks: int | None = None,
                    finance_tickers: list[str] | None = None) -> pd.DataFrame:
        result = orig(date, market=market, n_stocks=500)
        if result.empty:
            return result
        np.random.seed(hash(date) % 2**31)
        result["composite_score"] = np.random.uniform(0, 100, len(result))
        result = result.sort_values("composite_score", ascending=False)
        return result.head(n_stocks or settings.portfolio.n_stocks)

    screener.screen = rand_screen
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    engine.screener = screener
    result = engine.run(START, END)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["label"] = label

    pv = result["portfolio_value"]
    yearly = {}
    for year in range(2017, 2025):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    metrics["top5_tickers"] = ["(무작위)"]

    return metrics


def run_kospi_benchmark(label: str) -> dict:
    """KOSPI 벤치마크 (KODEX 200 Buy & Hold 시뮬레이션)

    KODEX 200 ETF (069500)를 초기 자금으로 매수 후 보유.
    """
    from data.collector import KRXDataCollector

    MultiFactorScreener._factor_cache.clear()
    collector = KRXDataCollector()
    ticker = "069500"  # KODEX 200

    # 시작~종료 OHLCV (ticker, start, end 순서)
    ohlcv = collector.get_ohlcv(
        ticker, START.replace("-", ""), END.replace("-", "")
    )
    if ohlcv.empty:
        logger.error("KODEX 200 데이터 조회 실패")
        return {"label": label, "cagr": 0, "mdd": 0, "sharpe": 0,
                "total_return": 0, "yearly": {}, "top5_tickers": ["KODEX200"]}

    # 종가 기반 포트폴리오 가치
    close_col = "종가" if "종가" in ohlcv.columns else "close"
    prices = ohlcv[close_col].dropna()
    if prices.empty:
        logger.error("KODEX 200 종가 없음")
        return {"label": label, "cagr": 0, "mdd": 0, "sharpe": 0,
                "total_return": 0, "yearly": {}, "top5_tickers": ["KODEX200"]}

    initial_cash = settings.portfolio.initial_cash
    shares = initial_cash / prices.iloc[0]
    portfolio_values = prices * shares
    portfolio_values.name = "portfolio_value"
    # 인덱스를 DatetimeIndex로 변환
    if not isinstance(portfolio_values.index, pd.DatetimeIndex):
        portfolio_values.index = pd.to_datetime(portfolio_values.index)
    returns = portfolio_values.pct_change().dropna()

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.summary(portfolio_values, returns)
    metrics["label"] = label

    yearly = {}
    for year in range(2017, 2025):
        yp = portfolio_values[portfolio_values.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    metrics["top5_tickers"] = ["KODEX200"]

    return metrics


def print_table(results: list[dict], title: str) -> None:
    """결과 테이블 출력"""
    labels = [r["label"] for r in results]
    w = 14 + 13 * len(labels)
    print(f"\n{'=' * w}")
    print(f"{title:^{w}}")
    print(f"{'=' * w}")

    header = f"{'':>12}"
    for lb in labels:
        header += f" | {lb:>10}"
    print(header)
    print("-" * w)

    for key, name in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                       ("sortino", "Sortino"), ("calmar", "Calmar"),
                       ("total_return", "Total")]:
        row = f"{name:>12}"
        for r in results:
            v = r.get(key, 0)
            if key in ("cagr", "mdd", "total_return"):
                row += f" | {v:>9.2%}"
            else:
                row += f" | {v:>9.3f}"
        print(row)

    # 무작위 대비 알파
    rand_cagr = None
    for r in results:
        if "무작위" in r["label"]:
            rand_cagr = r.get("cagr", 0)
            break
    if rand_cagr is not None:
        row = f"{'Alpha':>12}"
        for r in results:
            alpha = r.get("cagr", 0) - rand_cagr
            row += f" | {alpha:>+8.2%}p"
        print(row)

    # 상위 종목
    print()
    print(f"{'상위종목':>12}", end="")
    for r in results:
        tickers = r.get("top5_tickers", [])
        txt = ",".join(tickers[:3]) if tickers else "-"
        print(f" | {txt:>10}", end="")
    print()

    # 연도별
    print()
    header2 = f"{'연도':>12}"
    for lb in labels:
        header2 += f" | {lb:>10}"
    print(header2)
    print("-" * w)
    for year in range(2017, 2025):
        row = f"{year:>12}"
        for r in results:
            row += f" | {r['yearly'].get(year, 0):>9.1%}"
        print(row)
    print("=" * w)


def check_cache_clean() -> None:
    """캐시 상태 확인"""
    cache_size = len(MultiFactorScreener._factor_cache)
    print(f"\n[캐시 상태] _factor_cache 크기: {cache_size}")
    if cache_size > 0:
        print(f"  캐시 키: {list(MultiFactorScreener._factor_cache.keys())[:5]}")
        print("  >> 캐시를 클리어합니다.")
        MultiFactorScreener._factor_cache.clear()
    else:
        print("  >> 깨끗한 상태입니다.")


def experiment1() -> list[dict]:
    """실험 1: 벤치마크 확립"""
    print("\n" + "=" * 60)
    print(">>> 실험 1: 벤치마크 확립")
    print("=" * 60)
    backup = backup_settings()
    results = []

    # 1a. 무작위 포트폴리오
    restore_settings(backup)
    base_config()
    print("\n[1a] 무작위 포트폴리오 (20종목, 유니버스 내 랜덤)")
    results.append(run_random("무작위"))

    # 1b. KOSPI (KODEX 200 Buy & Hold)
    restore_settings(backup)
    base_config()
    print("\n[1b] KOSPI 벤치마크 (KODEX 200 Buy & Hold)")
    results.append(run_kospi_benchmark("KOSPI"))

    restore_settings(backup)
    print_table(results, "실험 1: 벤치마크 (2017-2024)")
    return results


def experiment2() -> list[dict]:
    """실험 2: 팩터별 단독 전략"""
    print("\n" + "=" * 60)
    print(">>> 실험 2: 팩터별 단독 전략")
    print("=" * 60)
    backup = backup_settings()
    results = []

    # 2a. Value 100%
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 1.0
    settings.factor_weights.momentum = 0.0
    settings.factor_weights.quality = 0.0
    print("\n[2a] Value 100% (V=1.0, M=0, Q=0)")
    results.append(run_bt("V100%"))

    # 2b. Momentum 100% (절대모멘텀 OFF)
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.0
    settings.factor_weights.momentum = 1.0
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False
    print("\n[2b] Momentum 100% (V=0, M=1.0, Q=0, 절대모멘텀 OFF)")
    results.append(run_bt("M100%"))

    # 2c. Quality 100%
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.0
    settings.factor_weights.momentum = 0.0
    settings.factor_weights.quality = 1.0
    print("\n[2c] Quality 100% (V=0, M=0, Q=1.0)")
    results.append(run_bt("Q100%"))

    restore_settings(backup)
    print_table(results, "실험 2: 팩터별 단독 전략 (2017-2024)")
    return results


def experiment3(rand_result: dict) -> list[dict]:
    """실험 3: 팩터 조합 전략"""
    print("\n" + "=" * 60)
    print(">>> 실험 3: 팩터 조합 전략")
    print("=" * 60)
    backup = backup_settings()
    results = []

    # 3a. 기존 프리셋 A (V=0.35, M=0.40, Q=0.25)
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.35
    settings.factor_weights.momentum = 0.40
    settings.factor_weights.quality = 0.25
    print("\n[3a] 프리셋 A (V=0.35, M=0.40, Q=0.25)")
    results.append(run_bt("A(균형)"))

    # 3b. V60% + M40% + Q0%
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.60
    settings.factor_weights.momentum = 0.40
    settings.factor_weights.quality = 0.0
    print("\n[3b] V60 + M40 (Quality 제거)")
    results.append(run_bt("V60M40"))

    # 3c. V70% + M30% + Q0%
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.70
    settings.factor_weights.momentum = 0.30
    settings.factor_weights.quality = 0.0
    print("\n[3c] V70 + M30")
    results.append(run_bt("V70M30"))

    # 3d. V50% + M50% + Q0%
    restore_settings(backup)
    base_config()
    settings.factor_weights.value = 0.50
    settings.factor_weights.momentum = 0.50
    settings.factor_weights.quality = 0.0
    print("\n[3d] V50 + M50")
    results.append(run_bt("V50M50"))

    restore_settings(backup)
    # 무작위 결과를 앞에 추가하여 알파 비교
    all_results = [rand_result] + results
    print_table(all_results, "실험 3: 팩터 조합 전략 (2017-2024)")
    return results


def final_summary(
    bench_results: list[dict],
    factor_results: list[dict],
    combo_results: list[dict],
) -> None:
    """전체 결과 통합 비교 테이블"""
    rand_result = bench_results[0]
    kospi_result = bench_results[1]
    rand_cagr = rand_result.get("cagr", 0)
    kospi_cagr = kospi_result.get("cagr", 0)

    all_results = bench_results + factor_results + combo_results

    print("\n" + "=" * 90)
    print("최종 통합 비교 테이블 (2017-2024, 분기 리밸런싱, 동일가중, 20종목, CB OFF)")
    print("=" * 90)

    print(f"\n{'전략':>12} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | "
          f"{'Sortino':>7} | {'Alpha':>8} | {'비고':>20}")
    print("-" * 90)

    for r in all_results:
        alpha = r.get("cagr", 0) - rand_cagr
        note = ""
        if r.get("cagr", 0) > kospi_cagr:
            note = "KOSPI 초과"
        if alpha > 0 and "무작위" not in r["label"]:
            note += " +알파"
        print(f"{r['label']:>12} | {r.get('cagr', 0):>7.2%} | {r.get('mdd', 0):>7.1%} | "
              f"{r.get('sharpe', 0):>7.3f} | {r.get('sortino', 0):>7.3f} | "
              f"{alpha:>+7.2%} | {note:>20}")

    # 연도별 비교
    print(f"\n{'연도별 수익률':^90}")
    print("-" * 90)
    header = f"{'연도':>6}"
    for r in all_results:
        header += f" | {r['label']:>8}"
    print(header)
    print("-" * 90)
    for year in range(2017, 2025):
        row = f"{year:>6}"
        for r in all_results:
            row += f" | {r['yearly'].get(year, 0):>7.1%}"
        print(row)
    print("=" * 90)

    # 종목 겹침 확인
    print(f"\n{'실험 간 상위 종목 비교':^60}")
    print("-" * 60)
    for r in all_results:
        tickers = r.get("top5_tickers", [])
        print(f"  {r['label']:>12}: {', '.join(tickers[:5])}")


def main() -> None:
    # 0. 캐시 상태 확인
    check_cache_clean()

    # 실험 1: 벤치마크
    bench = experiment1()

    # 실험 2: 팩터별 단독
    factors = experiment2()

    # 실험 3: 팩터 조합
    combos = experiment3(bench[0])  # 무작위 결과 전달

    # 최종 통합 요약
    final_summary(bench, factors, combos)


if __name__ == "__main__":
    main()
