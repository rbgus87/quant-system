"""python -m backtest 실행 지원

사용법:
  python -m backtest --start 2020-01-01 --end 2025-12-31 --cash 10000000
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer


def main() -> None:
    parser = argparse.ArgumentParser(description="멀티팩터 백테스트")
    parser.add_argument("--start", default="2020-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-12-31", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--cash", type=float, default=10_000_000, help="초기 자본금")
    args = parser.parse_args()

    # Windows CP949 인코딩 문제 방지
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(line_buffering=True)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    print(f"백테스트 시작: {args.start} ~ {args.end} (자본금: {args.cash:,.0f}원)")
    print("=" * 60)

    bt = MultiFactorBacktest(initial_cash=args.cash)
    results = bt.run(args.start, args.end)

    if results.empty:
        print("백테스트 결과 없음")
        return

    analyzer = PerformanceAnalyzer()
    pv = results["portfolio_value"]
    returns = pv.pct_change().dropna()

    cagr = analyzer.calculate_cagr(pv)
    mdd = analyzer.calculate_mdd(pv)
    sharpe = analyzer.calculate_sharpe(returns)
    calmar = analyzer.calculate_calmar(cagr, mdd)
    win_rate = analyzer.calculate_win_rate(returns)

    print("\n" + "=" * 60)
    print("백테스트 결과")
    print("=" * 60)
    print(f"  CAGR:      {cagr:.2%}")
    print(f"  MDD:       {mdd:.2%}")
    print(f"  Sharpe:    {sharpe:.3f}")
    print(f"  Calmar:    {calmar:.3f}")
    print(f"  승률:      {win_rate:.1%}")
    print(f"  최종 자산:  {pv.iloc[-1]:,.0f}원")
    print(f"  총 수익률:  {(pv.iloc[-1] / args.cash - 1):.2%}")


if __name__ == "__main__":
    main()
