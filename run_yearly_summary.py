# run_yearly_summary.py — 연도별 백테스트 성과 요약
"""
2017~2025년 각 연도별로 백테스트를 실행하고
CAGR, MDD, 샤프 비율을 한눈에 출력합니다.

Usage:
    python run_yearly_summary.py
    python run_yearly_summary.py --start-year 2020 --end-year 2024
    python run_yearly_summary.py --cash 50000000
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer

logger = logging.getLogger(__name__)


def run_yearly_summary(
    start_year: int = 2020,
    end_year: int = 2024,
    initial_cash: float = 0,
) -> list[dict]:
    """연도별 백테스트 실행 후 성과 요약 반환

    Args:
        start_year: 시작 연도
        end_year: 종료 연도
        initial_cash: 초기 자금 (0이면 config 기본값)

    Returns:
        [{year, cagr, mdd, sharpe}, ...]
    """
    if initial_cash <= 0:
        initial_cash = settings.portfolio.initial_cash

    analyzer = PerformanceAnalyzer()
    results: list[dict] = []

    for year in range(start_year, end_year + 1):
        # --auto-lead 효과: 1개월 선행하여 해당 연도 1월부터 매매 결과 포함
        actual_start = (datetime(year, 1, 1) - relativedelta(months=1)).strftime("%Y-%m-%d")
        end_date = f"{year}-12-31"

        logger.info(f"{'=' * 50}")
        logger.info(f"[{year}년] 백테스트 시작 (내부 시작: {actual_start})")

        try:
            engine = MultiFactorBacktest(initial_cash=initial_cash)
            result_df = engine.run(start_date=actual_start, end_date=end_date)

            portfolio_values = result_df["portfolio_value"]
            returns = result_df["returns"].dropna()

            cagr = analyzer.calculate_cagr(portfolio_values)
            mdd = analyzer.calculate_mdd(portfolio_values)
            sharpe = analyzer.calculate_sharpe(returns)

            results.append({
                "year": year,
                "cagr": cagr,
                "mdd": mdd,
                "sharpe": sharpe,
            })

        except Exception as e:
            logger.error(f"[{year}년] 백테스트 실패: {e}")
            results.append({
                "year": year,
                "cagr": None,
                "mdd": None,
                "sharpe": None,
            })

    return results


def print_summary(results: list[dict]) -> None:
    """연도별 성과를 테이블 형태로 출력"""
    print("\n" + "=" * 55)
    print("  연도별 백테스트 성과 요약")
    print("=" * 55)
    print(f"  {'연도':<6} {'CAGR':>10} {'MDD':>10} {'샤프':>8}")
    print("-" * 55)

    valid_results = []
    for r in results:
        year = r["year"]
        if r["cagr"] is None:
            print(f"  {year:<6} {'실패':>10} {'실패':>10} {'실패':>8}")
        else:
            cagr_str = f"{r['cagr'] * 100:+.2f}%"
            mdd_str = f"{r['mdd'] * 100:.2f}%"
            sharpe_str = f"{r['sharpe']:.3f}"
            print(f"  {year:<6} {cagr_str:>10} {mdd_str:>10} {sharpe_str:>8}")
            valid_results.append(r)

    # 평균 요약
    if valid_results:
        print("-" * 55)
        avg_cagr = sum(r["cagr"] for r in valid_results) / len(valid_results)
        avg_mdd = sum(r["mdd"] for r in valid_results) / len(valid_results)
        avg_sharpe = sum(r["sharpe"] for r in valid_results) / len(valid_results)
        print(f"  {'평균':<6} {avg_cagr * 100:+.2f}%{'':<4} {avg_mdd * 100:.2f}%{'':<4} {avg_sharpe:.3f}")

    print("=" * 55)


def main() -> None:
    parser = argparse.ArgumentParser(description="연도별 백테스트 성과 요약")
    parser.add_argument(
        "--start-year", type=int, default=2020, help="시작 연도 (기본: 2020)"
    )
    parser.add_argument(
        "--end-year", type=int, default=2024, help="종료 연도 (기본: 2024)"
    )
    default_cash = settings.portfolio.initial_cash
    parser.add_argument(
        "--cash",
        type=float,
        default=default_cash,
        help=f"초기 자금 (기본: {default_cash:,.0f})",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="백테스트 상세 로그 숨기기 (결과만 출력)"
    )
    args = parser.parse_args()

    setup_logging()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    results = run_yearly_summary(
        start_year=args.start_year,
        end_year=args.end_year,
        initial_cash=args.cash,
    )

    print_summary(results)


if __name__ == "__main__":
    main()
