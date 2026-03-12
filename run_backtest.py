# run_backtest.py — 백테스트 CLI 진입점
import argparse
import logging
import os
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from backtest.report import ReportGenerator

logger = logging.getLogger(__name__)


def run(
    start_date: str,
    end_date: str,
    initial_cash: float,
    label: str,
    report_path: str,
) -> None:
    """백테스트 실행 + 성과 분석 + HTML 리포트 생성

    Args:
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)
        initial_cash: 초기 자금
        label: 리포트 라벨
        report_path: HTML 리포트 저장 경로
    """
    engine = MultiFactorBacktest(initial_cash=initial_cash)
    result = engine.run(start_date=start_date, end_date=end_date)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)

    logger.info(f"\n=== {label} 성과 ===")
    for k, v in metrics.items():
        if isinstance(v, float):
            logger.info(f"  {k}: {v:.4f}")
        else:
            logger.info(f"  {k}: {v}")

    # HTML 리포트
    reporter = ReportGenerator()
    benchmark = reporter.fetch_kospi_benchmark(start_date, end_date)

    # 영문 리포트 (quantstats)
    reporter.generate_html(
        returns,
        benchmark_returns=benchmark if not benchmark.empty else None,
        output_path=report_path,
        title=f"멀티팩터 퀀트 — {label}",
    )

    # 한글 리포트
    kr_path = report_path.replace(".html", "_kr.html")
    bm_values = None
    if not benchmark.empty:
        bm_values = (1 + benchmark).cumprod() * initial_cash

    # 턴오버 로그 (engine이 result.attrs에 저장)
    turnover_log = result.attrs.get("turnover_log")

    reporter.generate_korean_html(
        portfolio_values=result["portfolio_value"],
        returns=returns,
        metrics=metrics,
        output_path=kr_path,
        title=f"멀티팩터 퀀트 — {label}",
        benchmark_values=bm_values,
        turnover_log=turnover_log,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="멀티팩터 퀀트 백테스트")
    parser.add_argument(
        "--mode",
        choices=["insample", "outsample", "both", "custom"],
        default="both",
        help="백테스트 모드 (기본: both)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="시작일 YYYY-MM-DD (--mode custom 시 필수). "
        "첫 달은 팩터 계산에 사용되어 실제 매매는 다음 달부터 시작됩니다. "
        "예: 2025년 1월부터 매매 결과를 보려면 --start 2024-12-01",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="종료일 YYYY-MM-DD (--mode custom 시 필수)",
    )
    parser.add_argument(
        "--auto-lead",
        action="store_true",
        default=False,
        help="start 날짜를 자동으로 1개월 앞당겨 첫 달부터 매매 결과 포함. "
        "예: --start 2025-01-01 --auto-lead → 내부적으로 2024-12-01 시작",
    )
    # config.yaml의 portfolio.initial_cash 사용 (기본 1000만원)
    default_cash = settings.portfolio.initial_cash
    parser.add_argument(
        "--cash",
        type=float,
        default=default_cash,
        help=f"초기 자금 (기본: {default_cash:,.0f}, config.yaml portfolio.initial_cash 연동)",
    )
    args = parser.parse_args()

    setup_logging()

    if args.mode == "custom":
        if not args.start or not args.end:
            parser.error("--mode custom 사용 시 --start, --end 필수")

        start = args.start
        if args.auto_lead:
            # 1개월 선행: 사용자가 원하는 달부터 매매 결과가 나오도록
            lead_dt = datetime.strptime(args.start, "%Y-%m-%d") - relativedelta(months=1)
            start = lead_dt.strftime("%Y-%m-%d")
            logger.info(f"--auto-lead: 시작일 {args.start} → {start} (1개월 선행)")

        run(
            start_date=start,
            end_date=args.end,
            initial_cash=args.cash,
            label=f"Custom ({args.start}~{args.end})",
            report_path="reports/custom_report.html",
        )
        return

    if args.mode in ("insample", "both"):
        run(
            start_date="2015-01-01",
            end_date="2020-12-31",
            initial_cash=args.cash,
            label="In-Sample (2015~2020)",
            report_path="reports/insample_report.html",
        )

    if args.mode in ("outsample", "both"):
        run(
            start_date="2021-01-01",
            end_date="2024-12-31",
            initial_cash=args.cash,
            label="Out-of-Sample (2021~2024)",
            report_path="reports/outsample_report.html",
        )


if __name__ == "__main__":
    main()
