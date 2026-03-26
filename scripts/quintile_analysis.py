"""팩터 퀸타일 분석 + 홀딩 버퍼 추적"""
import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd
import numpy as np

from config.logging_config import setup_logging
from config.settings import settings
from strategy.screener import MultiFactorScreener
from data.collector import KRXDataCollector

setup_logging()
logger = logging.getLogger(__name__)


def quintile_analysis() -> None:
    """1. 팩터 퀸타일 분석"""
    print("=" * 75)
    print("팩터 퀸타일 분석 (2023-12-28 기준, 이후 3M/6M 수익률)")
    print("=" * 75)

    date = "20231228"
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()

    # 넓은 유니버스 스코어 가져오기 (상위 200개)
    wide_df = screener.screen(date, n_stocks=200)
    if wide_df.empty:
        print("스크리닝 결과 없음")
        return

    collector = screener.collector
    tickers = wide_df.index.tolist()

    # 이후 수익률 계산 (2024-01-02 ~ 2024-06-28)
    print(f"\n유니버스: {len(tickers)}개 종목")
    print("수익률 계산 중...")

    rets_3m: dict[str, float] = {}
    rets_6m: dict[str, float] = {}
    for ticker in tickers:
        try:
            ohlcv = collector.get_ohlcv(ticker, "20240102", "20240701")
            if ohlcv is None or ohlcv.empty or len(ohlcv) < 5:
                continue
            start_p = ohlcv["close"].iloc[0]
            if start_p <= 0:
                continue
            # 3M (~63 거래일)
            idx_3m = min(62, len(ohlcv) - 1)
            rets_3m[ticker] = ohlcv["close"].iloc[idx_3m] / start_p - 1
            # 6M (~126 거래일)
            idx_6m = min(125, len(ohlcv) - 1)
            rets_6m[ticker] = ohlcv["close"].iloc[idx_6m] / start_p - 1
        except Exception:
            continue

    ret_3m_s = pd.Series(rets_3m)
    ret_6m_s = pd.Series(rets_6m)

    # 공통 종목만
    common = wide_df.index.intersection(ret_3m_s.index)
    scores_df = wide_df.loc[common].copy()
    scores_df["ret_3m"] = ret_3m_s.reindex(common)
    scores_df["ret_6m"] = ret_6m_s.reindex(common)
    scores_df = scores_df.dropna(subset=["ret_3m"])

    print(f"수익률 유효 종목: {len(scores_df)}개\n")

    # --- 복합 스코어 퀸타일 ---
    def print_quintile(score_col: str, label: str) -> None:
        if score_col not in scores_df.columns:
            print(f"{label}: 데이터 없음\n")
            return
        valid = scores_df[[score_col, "ret_3m", "ret_6m"]].dropna()
        if len(valid) < 10:
            print(f"{label}: 유효 종목 {len(valid)}개 (부족)\n")
            return

        valid["quintile"] = pd.qcut(
            valid[score_col], 5, labels=["Q5(하위)", "Q4", "Q3", "Q2", "Q1(상위)"]
        )

        print(f"--- {label} 퀸타일 ---")
        print(f"{'퀸타일':>12} | {'종목수':>5} | {'3M수익률':>8} | {'6M수익률':>8} | {'3M승률':>6}")
        print("-" * 55)
        for q in ["Q1(상위)", "Q2", "Q3", "Q4", "Q5(하위)"]:
            group = valid[valid["quintile"] == q]
            n = len(group)
            r3 = group["ret_3m"].mean()
            r6 = group["ret_6m"].mean()
            wr3 = (group["ret_3m"] > 0).mean()
            print(f"{q:>12} | {n:>5} | {r3:>+7.1%} | {r6:>+7.1%} | {wr3:>5.0%}")

        q1 = valid[valid["quintile"] == "Q1(상위)"]["ret_3m"].mean()
        q5 = valid[valid["quintile"] == "Q5(하위)"]["ret_3m"].mean()
        spread = q1 - q5
        if spread > 0.02:
            verdict = "팩터 유효 (Q1 > Q5)"
        elif spread < -0.02:
            verdict = "!! 팩터 역전 (Q1 < Q5)"
        else:
            verdict = "팩터 무력 (차이 미미)"
        print(f"  Q1-Q5 스프레드(3M): {spread:+.1%} → {verdict}")
        print()

    print_quintile("composite_score", "복합 스코어 (Composite)")
    print_quintile("value_score", "밸류 (Value)")
    print_quintile("momentum_score", "모멘텀 (Momentum)")
    print_quintile("quality_score", "퀄리티 (Quality)")


def buffer_trace() -> None:
    """2. 홀딩 버퍼 코드 추적"""
    print("=" * 75)
    print("홀딩 버퍼 추적 (2023 Q1 리밸런싱 상세)")
    print("=" * 75)

    date = "20230331"
    n_stocks = settings.portfolio.n_stocks
    buffer_ratio = settings.portfolio.holding_buffer_ratio
    buffer_n = int(n_stocks * buffer_ratio)

    print(f"\n설정: n_stocks={n_stocks}, buffer_ratio={buffer_ratio}, buffer_n={buffer_n}")

    # 직전 분기(2022 Q4) 포트폴리오를 시뮬레이션
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()

    prev_df = screener.screen("20221229", n_stocks=n_stocks)
    prev_holdings = set(prev_df.index.tolist()) if not prev_df.empty else set()
    print(f"\n직전 보유 ({len(prev_holdings)}개): {sorted(prev_holdings)[:5]}...")

    # 현재 분기: 넓은 후보 (buffer_n개)
    MultiFactorScreener._factor_cache.clear()
    wide_df = screener.screen(date, n_stocks=buffer_n)
    print(f"screener.screen(n={buffer_n}) 반환: {len(wide_df)}개 종목")

    if wide_df.empty:
        print("스크리닝 실패")
        return

    wide_candidates = wide_df.index.tolist()
    top_n = set(wide_candidates[:n_stocks])
    buffer_set = set(wide_candidates[:buffer_n])

    # 버퍼 분석
    held_in_buffer = prev_holdings & buffer_set
    held_outside = prev_holdings - buffer_set
    held_in_top = prev_holdings & top_n

    print(f"\n--- 버퍼 분석 ---")
    print(f"기존 보유 {len(prev_holdings)}개 중:")
    print(f"  상위 {n_stocks}위 이내: {len(held_in_top)}개 (교체 불필요)")
    print(f"  상위 {buffer_n}위 이내 (버퍼): {len(held_in_buffer)}개 (유지)")
    print(f"  버퍼 밖: {len(held_outside)}개 (매도 대상)")

    # 최종 포트폴리오 구성
    keep = held_in_buffer
    new_portfolio = list(keep)
    for ticker in wide_candidates:
        if len(new_portfolio) >= n_stocks:
            break
        if ticker not in keep:
            new_portfolio.append(ticker)

    new_set = set(new_portfolio)
    actual_sells = prev_holdings - new_set
    actual_buys = new_set - prev_holdings
    actual_kept = prev_holdings & new_set

    print(f"\n--- 최종 결과 ---")
    print(f"유지: {len(actual_kept)}개")
    print(f"매도: {len(actual_sells)}개")
    print(f"매수: {len(actual_buys)}개")
    print(f"턴오버: {(len(actual_sells) + len(actual_buys)) / (2 * max(len(prev_holdings), len(new_set), 1)):.0%}")

    # 버퍼 없이 순수 상위 20개일 때와 비교
    no_buffer_sells = prev_holdings - top_n
    no_buffer_buys = top_n - prev_holdings
    no_buffer_to = (len(no_buffer_sells) + len(no_buffer_buys)) / (2 * max(len(prev_holdings), len(top_n), 1))

    print(f"\n--- 버퍼 없이 (순수 상위 {n_stocks}개) ---")
    print(f"매도: {len(no_buffer_sells)}개, 매수: {len(no_buffer_buys)}개")
    print(f"턴오버: {no_buffer_to:.0%}")

    print(f"\n버퍼 효과: 턴오버 {no_buffer_to:.0%} → "
          f"{(len(actual_sells) + len(actual_buys)) / (2 * max(len(prev_holdings), len(new_set), 1)):.0%} "
          f"({len(no_buffer_sells) - len(actual_sells)}건 교체 절감)")
    print()


if __name__ == "__main__":
    quintile_analysis()
    buffer_trace()
