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
        self.return_calc = ReturnCalculator(collector=self.collector)
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
            # 1. 데이터 수집 (ALL = KOSPI+KOSDAQ 통합)
            markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
            logger.info(f"[{date}] 스크리닝 시작 — {'+'.join(markets)}")

            fundamentals_list = []
            market_cap_list = []
            for m in markets:
                f = self.collector.get_fundamentals_all(date, m)
                if not f.empty:
                    fundamentals_list.append(f)
                mc = self.collector.get_market_cap(date, m)
                if not mc.empty:
                    market_cap_list.append(mc)

            fundamentals = pd.concat(fundamentals_list) if fundamentals_list else pd.DataFrame()
            market_cap = pd.concat(market_cap_list) if market_cap_list else pd.DataFrame()
            if not market_cap.empty:
                market_cap = market_cap[~market_cap.index.duplicated(keep="first")]

            # 펀더멘털 유무에 따라 분기
            has_fundamentals = not fundamentals.empty
            if has_fundamentals:
                fundamentals = fundamentals[~fundamentals.index.duplicated(keep="first")]
                cleaned = self.processor.clean_fundamentals(fundamentals)
            else:
                cleaned = pd.DataFrame()
                logger.warning(f"[{date}] 펀더멘털 없음 → 모멘텀 전용 모드")

            # 2. 유니버스 결정
            if not cleaned.empty:
                universe_tickers = cleaned.index.tolist()
            elif not market_cap.empty:
                universe_tickers = market_cap.index.tolist()
            else:
                logger.error(f"[{date}] 유니버스 구성 데이터 없음")
                return pd.DataFrame()

            # 거래정지 종목 감지
            suspended = self.collector.get_suspended_tickers(universe_tickers, date)

            # 유동성 데이터 (평균 거래대금)
            avg_tv = None
            min_tv = settings.universe.min_avg_trading_value
            if min_tv > 0:
                avg_tv = self.collector.get_avg_trading_value(universe_tickers, date)

            tickers = self.processor.filter_universe(
                tickers=universe_tickers,
                market_cap=market_cap,
                fundamentals=cleaned if not cleaned.empty else None,
                min_cap_percentile=settings.universe.min_market_cap_percentile,
                finance_tickers=finance_tickers,
                avg_trading_value=avg_tv,
                min_avg_trading_value=min_tv,
                suspended_tickers=suspended,
            )
            logger.info(f"필터 후 유니버스: {len(tickers)}개 종목")

            if not tickers:
                logger.error(f"[{date}] 필터 후 유효 종목 없음")
                return pd.DataFrame()

            # 3. 팩터 계산
            value_score = pd.Series(dtype=float, name="value_score")
            quality_score = pd.Series(dtype=float, name="quality_score")

            if not cleaned.empty:
                filtered_fund = cleaned.loc[cleaned.index.isin(tickers)]

                # F-Score 필터 적용 (가치 함정 방어)
                if settings.quality.fscore_enabled:
                    fscore = self.quality_factor.calc_fscore(filtered_fund)
                    filtered_fund = self.quality_factor.apply_fscore_filter(
                        filtered_fund, fscore
                    )

                value_score = self.value_factor.calculate(filtered_fund)
                quality_score = self.quality_factor.calculate(filtered_fund)

            # 모멘텀
            returns_12m = self.return_calc.get_returns_for_universe(
                tickers, date, lookback_months=12, skip_months=1
            )

            # 듀얼 모멘텀: 절대 모멘텀 필터 (하락장 방어)
            if settings.momentum.absolute_momentum_enabled:
                returns_12m = self.momentum_factor.apply_absolute_momentum(returns_12m)

            momentum_score = self.momentum_factor.calculate(returns_12m)

            # 4. 복합 스코어 + 상위 N개
            min_factors = 2 if has_fundamentals else 1
            composite_df = self.composite.calculate(
                value_score, momentum_score, quality_score,
                min_factor_count=min_factors,
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
