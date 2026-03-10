# backtest/report.py
import matplotlib

matplotlib.use("Agg")

import pandas as pd
import os
import logging
from typing import Optional
import quantstats as qs
import quantstats.reports as _qs_reports

logger = logging.getLogger(__name__)

# quantstats 0.0.81 + pandas 3.x 호환성 패치
# metrics.replace([-0, "-0"], 0) 이 pandas 3.x에서 ValueError 발생
_orig_metrics = _qs_reports.metrics


def _patched_metrics(*args, **kwargs):
    _orig_replace = pd.DataFrame.replace

    def _safe_replace(self, to_replace=None, value=None, **kw):
        try:
            return _orig_replace(self, to_replace, value, **kw)
        except ValueError:
            # pandas 3.x: 컬럼별 순회하여 안전하게 replace
            result = self.copy()
            for col in result.columns:
                try:
                    result[col] = result[col].replace(to_replace, value)
                except (ValueError, TypeError):
                    pass
            return result

    pd.DataFrame.replace = _safe_replace
    try:
        return _orig_metrics(*args, **kwargs)
    finally:
        pd.DataFrame.replace = _orig_replace


_qs_reports.metrics = _patched_metrics


class ReportGenerator:
    """백테스트 성과 HTML 리포트 생성"""

    def generate_html(
        self,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series] = None,
        output_path: str = "reports/backtest_report.html",
        title: str = "멀티팩터 퀀트 전략",
    ) -> None:
        """quantstats HTML 리포트 생성

        Args:
            returns: 일별 수익률 Series (index=date)
            benchmark_returns: 벤치마크 수익률 Series (선택)
            output_path: 출력 파일 경로
            title: 리포트 제목
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 벤치마크 날짜 정렬
        benchmark = None
        if benchmark_returns is not None and not benchmark_returns.empty:
            # 공통 날짜로 정렬
            common_dates = returns.index.intersection(benchmark_returns.index)
            if len(common_dates) > 10:
                benchmark = benchmark_returns.reindex(common_dates)
                logger.info(
                    f"벤치마크 날짜 정렬: {len(benchmark_returns)} → {len(common_dates)}개"
                )
            else:
                logger.warning("벤치마크 공통 날짜 부족, 벤치마크 없이 생성")

        try:
            qs.reports.html(
                returns,
                benchmark=benchmark,
                title=title,
                output=output_path,
            )
            logger.info(f"HTML 리포트 저장: {output_path}")
        except Exception as e:
            logger.error(f"HTML 리포트 생성 실패: {e}", exc_info=True)
            raise

    def fetch_kospi_benchmark(self, start_date: str, end_date: str) -> pd.Series:
        """KOSPI 벤치마크 수익률 조회 (FinanceDataReader)

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            KOSPI 일별 수익률 Series
        """
        try:
            import FinanceDataReader as fdr

            kospi = fdr.DataReader("KS11", start_date, end_date)
            returns = kospi["Close"].pct_change().dropna()
            logger.info(f"KOSPI 벤치마크 로드: {len(returns)}일")
            return returns
        except Exception as e:
            logger.warning(f"KOSPI 벤치마크 로드 실패: {e}")
            return pd.Series(dtype=float)
