# strategy/screener.py
import pandas as pd
import logging
from typing import Optional

from config.settings import settings
from data.collector import KRXDataCollector, ReturnCalculator
from data.processor import DataProcessor
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite

logger = logging.getLogger(__name__)


class MultiFactorScreener:
    """멀티팩터 종목 스크리닝 통합 파이프라인

    유니버스 조회 → 데이터 수집 → 전처리 → 팩터 계산 → 상위 N개 반환
    """

    def __init__(self, request_delay: float = 0.5) -> None:
        self.collector = KRXDataCollector(request_delay=request_delay)
        self.return_calc = ReturnCalculator(request_delay=0.3)
        self.processor = DataProcessor()
        self.value_factor = ValueFactor()
        self.momentum_factor = MomentumFactor()
        self.quality_factor = QualityFactor()
        self.composite = MultiFactorComposite()

    def screen(
        self,
        date: str,
        market: Optional[str] = None,
        n_stocks: Optional[int] = None,
        finance_tickers: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """멀티팩터 스크리닝 실행

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: 시장 (기본: settings.universe.market)
            n_stocks: 선정 종목 수 (기본: settings.portfolio.n_stocks)
            finance_tickers: 금융주 종목 코드 리스트

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score,
            quality_score, composite_score, weight])
            빈 DataFrame 반환 시 에러 발생한 것
        """
        market = market or settings.universe.market
        n_stocks = n_stocks or settings.portfolio.n_stocks

        try:
            # 1. 데이터 수집
            logger.info(f"[{date}] 스크리닝 시작 — {market}")

            fundamentals = self.collector.get_fundamentals_all(date, market)
            if fundamentals.empty:
                logger.error(f"[{date}] 기본 지표 데이터 없음")
                return pd.DataFrame()

            market_cap = self.collector.get_market_cap(date, market)

            # 2. 전처리
            cleaned = self.processor.clean_fundamentals(fundamentals)
            tickers = self.processor.filter_universe(
                tickers=cleaned.index.tolist(),
                market_cap=market_cap,
                fundamentals=cleaned,
                min_cap_percentile=settings.universe.min_market_cap_percentile,
                finance_tickers=finance_tickers,
            )
            logger.info(f"필터 후 유니버스: {len(tickers)}개 종목")

            if not tickers:
                logger.error(f"[{date}] 필터 후 유효 종목 없음")
                return pd.DataFrame()

            # 3. 팩터 계산
            # 밸류
            value_score = self.value_factor.calculate(
                cleaned.loc[cleaned.index.isin(tickers)]
            )

            # 모멘텀
            returns_12m = self.return_calc.get_returns_for_universe(
                tickers, date, lookback_months=12, skip_months=1
            )
            momentum_score = self.momentum_factor.calculate(returns_12m)

            # 퀄리티
            quality_score = self.quality_factor.calculate(
                cleaned.loc[cleaned.index.isin(tickers)]
            )

            # 4. 복합 스코어 + 상위 N개
            composite_df = self.composite.calculate(
                value_score, momentum_score, quality_score
            )

            if composite_df.empty:
                logger.error(f"[{date}] 복합 스코어 계산 결과 없음")
                return pd.DataFrame()

            # 유니버스 필터 (시가총액 + 금융주) 재적용
            if not market_cap.empty:
                composite_df = self.composite.apply_universe_filter(
                    composite_df,
                    (
                        market_cap["market_cap"]
                        if "market_cap" in market_cap.columns
                        else market_cap
                    ),
                    finance_tickers=finance_tickers,
                )

            portfolio = self.composite.select_top(composite_df, n=n_stocks)

            logger.info(f"[{date}] 스크리닝 완료: {len(portfolio)}개 종목 선정")
            return portfolio

        except Exception as e:
            logger.error(f"[{date}] 스크리닝 실패: {e}", exc_info=True)
            return pd.DataFrame()
