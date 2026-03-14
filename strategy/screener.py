# strategy/screener.py
import pandas as pd
import logging
from typing import Optional

from config.settings import settings
from config.calendar import previous_krx_business_day
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

            # 데이터 없으면 직전 영업일로 자동 폴백 (최대 5일)
            if not fundamentals_list and not market_cap_list:
                from datetime import datetime as _dt

                original_date = date
                base_dt = _dt.strptime(date, "%Y%m%d").date()
                for attempt in range(5):
                    prev_ts = previous_krx_business_day(base_dt)
                    fallback_date = prev_ts.strftime("%Y%m%d")
                    logger.info(
                        f"[{original_date}] 데이터 없음 → "
                        f"직전 영업일 {fallback_date} 폴백 (시도 {attempt + 1}/5)"
                    )
                    for m in markets:
                        f = self.collector.get_fundamentals_all(fallback_date, m)
                        if not f.empty:
                            fundamentals_list.append(f)
                        mc = self.collector.get_market_cap(fallback_date, m)
                        if not mc.empty:
                            market_cap_list.append(mc)
                    if fundamentals_list or market_cap_list:
                        date = fallback_date
                        logger.info(f"[{fallback_date}] 폴백 성공")
                        break
                    base_dt = prev_ts.date() if hasattr(prev_ts, 'date') else prev_ts

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

            # 변동성 필터: 고변동성 종목 제외
            if settings.volatility.filter_enabled and tickers:
                tickers = self._apply_volatility_filter(tickers, date)
                logger.info(f"변동성 필터 후: {len(tickers)}개 종목")

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

            # 모멘텀 (멀티기간: 12M 60% + 6M 30% + 3M 10%)
            # 12M 데이터로 한 번 조회 후 6M은 슬라이싱으로 재사용
            multi_returns = self.return_calc.get_returns_multi_period(
                tickers, date, lookback_months_list=[12, 6], skip_months=1
            )
            returns_12m = multi_returns[12]
            returns_6m = multi_returns[6]

            # 듀얼 모멘텀: 절대 모멘텀 필터 (하락장 방어, 12M 기준)
            if settings.momentum.absolute_momentum_enabled:
                returns_12m = self.momentum_factor.apply_absolute_momentum(returns_12m)

            momentum_score = self.momentum_factor.calculate(
                returns_12m, returns_6m=returns_6m
            )

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

    def _apply_volatility_filter(
        self, tickers: list[str], date: str
    ) -> list[str]:
        """고변동성 종목을 유니버스에서 제외 (벌크 DB 조회)

        DB에서 전체 종목의 종가를 한 번에 벌크 조회한 뒤,
        각 종목의 일별 수익률 표준편차(연환산)를 계산하고
        상위 max_percentile 이상인 종목을 제거합니다.

        Args:
            tickers: 필터 대상 종목 리스트
            date: 기준 날짜 (YYYYMMDD)

        Returns:
            변동성 필터 통과 종목 리스트
        """
        import numpy as np
        from datetime import datetime, timedelta

        vol_cfg = settings.volatility
        lookback = vol_cfg.lookback_days

        # lookback 기간의 시작일 계산 (영업일 약 1.5배 캘린더일)
        end_dt = datetime.strptime(date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=int(lookback * 1.5))

        sd = start_dt.date()
        ed = end_dt.date()

        # 벌크 DB 조회 (1회 쿼리로 전체 종목 조회)
        bulk_df = self.collector.storage.load_daily_prices_bulk(tickers, sd, ed)

        volatilities: dict[str, float] = {}
        min_data_points = lookback // 2  # 최소 절반 이상 데이터 필요
        remaining: list[str] = []

        if not bulk_df.empty:
            bulk_df = bulk_df.sort_values(["ticker", "date"])
            for ticker, group in bulk_df.groupby("ticker"):
                closes = group["close"].dropna()
                if len(closes) < min_data_points:
                    continue
                daily_returns = closes.pct_change().dropna()
                if len(daily_returns) < min_data_points:
                    continue
                ann_vol = float(daily_returns.std() * np.sqrt(252))
                volatilities[str(ticker)] = ann_vol

            # 벌크에서 누락된 종목 (DB에 데이터 없음)
            found = set(bulk_df["ticker"].unique())
            remaining = [t for t in tickers if t not in found]
        else:
            remaining = list(tickers)

        # DB 미스 종목만 개별 pykrx 폴백 (소수)
        if remaining:
            start_str = start_dt.strftime("%Y%m%d")
            for ticker in remaining:
                try:
                    df = self.collector.get_ohlcv(ticker, start_str, date)
                    if df is None or df.empty or "close" not in df.columns:
                        continue
                    closes = df["close"].dropna()
                    if len(closes) < min_data_points:
                        continue
                    daily_returns = closes.pct_change().dropna()
                    if len(daily_returns) < min_data_points:
                        continue
                    ann_vol = float(daily_returns.std() * np.sqrt(252))
                    volatilities[ticker] = ann_vol
                except Exception as e:
                    logger.debug(f"변동성 계산 실패 ({ticker}): {e}")
                    continue

        if not volatilities:
            logger.warning("변동성 계산 가능한 종목 없음 — 필터 스킵")
            return tickers

        vol_series = pd.Series(volatilities)
        threshold = float(np.percentile(vol_series.values, vol_cfg.max_percentile))

        passed = vol_series[vol_series <= threshold].index.tolist()
        filtered_count = len(vol_series) - len(passed)

        if filtered_count > 0:
            # 제거된 종목 중 변동성 상위 5개 로깅
            removed = vol_series[vol_series > threshold].sort_values(ascending=False)
            top_removed = removed.head(5)
            names = [
                f"{t}({self.collector.get_ticker_name(t)})={v:.0%}"
                for t, v in top_removed.items()
            ]
            logger.info(
                f"변동성 필터: {filtered_count}개 제외 "
                f"(기준: 연환산 {threshold:.1%}, 상위 "
                f"{100 - vol_cfg.max_percentile:.0f}%), "
                f"제외 예시: {', '.join(names)}"
            )

        # 변동성 계산 불가 종목은 유지 (데이터 부족은 제외 사유 아님)
        no_vol_tickers = [t for t in tickers if t not in volatilities]
        return passed + no_vol_tickers
