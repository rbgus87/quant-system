# factors/quality.py
import pandas as pd
import numpy as np
import logging
from typing import Optional

from config.settings import settings
from factors.utils import weighted_average_nan_safe

logger = logging.getLogger(__name__)


class QualityFactor:
    """퀄리티 팩터 계산 (v2.0)

    구성 지표:
    - GP/A = 매출총이익 / 총자산 (Novy-Marx 2013, Value와 음의 상관 → 분산 효과)
    - Earnings Yield = 1 / PER (이익수익률)
    - F-Score (간소화 5점 피오트로스키, 0~100 정규화)
    """

    # F-Score 최대 점수 (calc_fscore의 5점 만점 기준)
    FSCORE_MAX = 5

    def calculate(
        self,
        fundamentals: pd.DataFrame,
        debt_ratio: Optional[pd.Series] = None,
    ) -> pd.Series:
        """복합 퀄리티 스코어 계산

        Args:
            fundamentals: DataFrame (index=ticker, EPS·BPS·PER·GROSS_PROFIT·TOTAL_ASSETS 등)
            debt_ratio: 부채비율 Series (선택, index=ticker) — 사용 시 가중치 재분배

        Returns:
            Series (index=ticker, values=quality_score 0~100)
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # GP/A 스코어 (40% 가중)
        gpa_score = self._calc_gpa_score(fundamentals)
        if not gpa_score.empty:
            score_parts["gpa"] = (gpa_score, 0.40)

        # Earnings Yield 스코어 (30% 가중)
        ey_score = self._calc_earnings_yield_score(fundamentals)
        if not ey_score.empty:
            score_parts["earnings_yield"] = (ey_score, 0.30)

        # F-Score 스코어 (30% 가중, 0~100 정규화)
        fscore_raw = self.calc_fscore(fundamentals)
        if not fscore_raw.empty:
            fscore_normalized = (fscore_raw / self.FSCORE_MAX) * 100
            score_parts["fscore"] = (fscore_normalized, 0.30)

        # 부채비율 역수 스코어 (데이터 있을 때만, 가중치 재분배)
        if debt_ratio is not None and not debt_ratio.empty:
            d = debt_ratio[debt_ratio >= 0]
            d = d.clip(upper=d.quantile(0.99))
            debt_score = (1 / (d + 1)).rank(pct=True) * 100
            score_parts["debt"] = (debt_score, 0.20)

        if not score_parts:
            logger.warning("퀄리티 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="quality_score")

        result = weighted_average_nan_safe(score_parts)
        result.name = "quality_score"
        logger.info(f"퀄리티 스코어 계산 완료: {len(result)}개 종목")
        return result

    @staticmethod
    def _calc_gpa_score(fundamentals: pd.DataFrame) -> pd.Series:
        """GP/A (또는 OP/A 폴백) 순위 스코어

        우선순위:
        1. GP/A = 매출총이익(GROSS_PROFIT) / 총자산(TOTAL_ASSETS)
        2. OP/A = 영업이익(OPERATING_INCOME) / 총자산 (fnlttMultiAcnt 폴백)
        3. ROE = EPS / BPS (최종 폴백)

        Args:
            fundamentals: DataFrame

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        ta_col = "TOTAL_ASSETS"
        has_total_assets = ta_col in fundamentals.columns and fundamentals[ta_col].notna().any()

        # 1순위: GP/A (매출총이익 / 총자산)
        if has_total_assets and "GROSS_PROFIT" in fundamentals.columns:
            gp = fundamentals["GROSS_PROFIT"]
            if gp.notna().any():
                result = QualityFactor._ratio_score(gp, fundamentals[ta_col])
                if not result.empty:
                    return result

        # 2순위: OP/A (영업이익 / 총자산) — fnlttMultiAcnt에서 제공
        if has_total_assets and "OPERATING_INCOME" in fundamentals.columns:
            oi = fundamentals["OPERATING_INCOME"]
            if oi.notna().any():
                logger.debug("GP/A 불가 -> OP/A(영업이익/총자산) 폴백")
                result = QualityFactor._ratio_score(oi, fundamentals[ta_col])
                if not result.empty:
                    return result

        # 3순위: ROE (EPS / BPS)
        if "EPS" in fundamentals.columns and "BPS" in fundamentals.columns:
            logger.debug("GP/A, OP/A 불가 -> ROE 폴백")
            return QualityFactor._calc_roe_score_fallback(fundamentals)

        return pd.Series(dtype=float)

    @staticmethod
    def _ratio_score(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
        """분자/분모 비율의 순위 스코어 계산 (공통 유틸)

        Args:
            numerator: 분자 Series (매출총이익, 영업이익 등)
            denominator: 분모 Series (총자산 등, > 0만 유효)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        valid = denominator[denominator > 0].index
        if len(valid) == 0:
            return pd.Series(dtype=float)

        ratio = numerator[valid] / denominator[valid]
        ratio = ratio.dropna()
        if ratio.empty:
            return pd.Series(dtype=float)

        lower = ratio.quantile(0.01)
        upper = ratio.quantile(0.99)
        ratio = ratio.clip(lower=lower, upper=upper)
        return ratio.rank(pct=True) * 100

    @staticmethod
    def _calc_roe_score_fallback(fundamentals: pd.DataFrame) -> pd.Series:
        """ROE 폴백: GP/A 데이터가 없을 때 사용

        Args:
            fundamentals: DataFrame (EPS, BPS 컬럼 필요)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        eps = fundamentals["EPS"]
        bps = fundamentals["BPS"]

        valid = bps[bps > 0].index
        roe = (eps[valid] / bps[valid]) * 100
        roe = roe.clip(lower=-50, upper=100)

        return roe.rank(pct=True) * 100

    @staticmethod
    def _calc_earnings_yield_score(fundamentals: pd.DataFrame) -> pd.Series:
        """Earnings Yield (1/PER) 순위 스코어

        PER가 양수인 종목만 대상 (적자 기업 제외)
        높은 이익수익률 = 높은 스코어

        Args:
            fundamentals: DataFrame (PER 컬럼 필요)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        if "PER" not in fundamentals.columns:
            return pd.Series(dtype=float)

        per = fundamentals["PER"]
        valid = per[per > 0]
        if valid.empty:
            return pd.Series(dtype=float)

        ey = 1 / valid  # Earnings Yield
        ey = ey.clip(upper=ey.quantile(0.99))
        return ey.rank(pct=True) * 100

    @staticmethod
    def calc_fscore(fundamentals: pd.DataFrame) -> pd.Series:
        """간소화 F-Score 계산 (5점 만점, 피오트로스키 기반)

        현재 보유한 펀더멘털 데이터만으로 계산 가능한 5개 항목:
          1. 수익성: ROE > 0 (+1)
          2. 흑자: PER > 0 (EPS 양수) (+1)
          3. 배당: DIV > 0 (배당 지급) (+1)
          4. 가치: PBR < 유니버스 중앙값 (+1)
          5. 수익 효율: ROE > 유니버스 중앙값 (+1)

        Args:
            fundamentals: DataFrame (index=ticker, 필요 컬럼: EPS, BPS, PER, PBR, DIV)

        Returns:
            Series (index=ticker, values=0~5 정수 F-Score)
        """
        if fundamentals.empty:
            return pd.Series(dtype=int, name="fscore")

        fscore = pd.Series(0, index=fundamentals.index, name="fscore")

        # 1. 수익성: ROE > 0
        if "EPS" in fundamentals.columns and "BPS" in fundamentals.columns:
            eps = fundamentals["EPS"]
            bps = fundamentals["BPS"]
            valid_bps = bps > 0
            roe = pd.Series(np.nan, index=fundamentals.index)
            roe[valid_bps] = eps[valid_bps] / bps[valid_bps]
            fscore += (roe > 0).astype(int)

            # 5. 수익 효율: ROE > 유니버스 중앙값
            roe_median = roe[roe.notna()].median()
            if not np.isnan(roe_median):
                fscore += (roe > roe_median).astype(int)

        # 2. 흑자: PER > 0 (= EPS 양수)
        if "PER" in fundamentals.columns:
            fscore += (fundamentals["PER"] > 0).astype(int)

        # 3. 배당 지급: DIV > 0
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].fillna(0)
            fscore += (div > 0).astype(int)

        # 4. 가치: PBR < 유니버스 중앙값
        if "PBR" in fundamentals.columns:
            pbr = fundamentals["PBR"]
            pbr_valid = pbr[pbr > 0]
            if not pbr_valid.empty:
                pbr_median = pbr_valid.median()
                fscore += ((pbr > 0) & (pbr < pbr_median)).astype(int)

        logger.info(
            f"F-Score 계산 완료: {len(fscore)}개 종목, "
            f"평균={fscore.mean():.1f}, 분포={dict(fscore.value_counts().sort_index())}"
        )
        return fscore

    @staticmethod
    def apply_fscore_filter(
        fundamentals: pd.DataFrame,
        fscore: pd.Series,
        min_fscore: Optional[int] = None,
    ) -> pd.DataFrame:
        """F-Score 기준 미달 종목 제거

        Args:
            fundamentals: 펀더멘털 DataFrame
            fscore: F-Score Series
            min_fscore: 최소 F-Score (기본: settings.quality.min_fscore)

        Returns:
            필터링된 fundamentals DataFrame
        """
        if min_fscore is None:
            min_fscore = settings.quality.min_fscore

        if fscore.empty:
            return fundamentals

        passing = fscore[fscore >= min_fscore].index
        before = len(fundamentals)
        filtered = fundamentals[fundamentals.index.isin(passing)]
        removed = before - len(filtered)

        if removed > 0:
            logger.info(
                f"F-Score 필터: {before} → {len(filtered)}개 종목 "
                f"({removed}개 제거, 기준={min_fscore}점 이상)"
            )

        return filtered

    @staticmethod
    def detect_eps_flip(
        storage,
        tickers: list[str],
        as_of_date: str,
        lookback_months: Optional[int] = None,
        min_change_pct: Optional[float] = None,
    ) -> set[str]:
        """최근 N개월 EPS 부호 반전 + |변동률| 임계 초과 종목 탐지.

        005620 유형 사례 (분기보고서 공시 직후 적자→흑자 급변) 사전 배제용.
        동일 회사의 월별 fundamental 시계열을 훑어 부호가 바뀌고 변동폭이 큰 경우를 반환.

        Args:
            storage: DataStorage 인스턴스
            tickers: 검사 대상 종목 리스트
            as_of_date: 기준일 (YYYYMMDD)
            lookback_months: 시계열 조회 기간 (기본: settings)
            min_change_pct: 변동률 임계값 (기본: settings, 1.5 = 150%)

        Returns:
            부호 반전 감지된 ticker 집합
        """
        from datetime import datetime, timedelta

        if lookback_months is None:
            lookback_months = settings.quality.eps_flip_lookback_months
        if min_change_pct is None:
            min_change_pct = settings.quality.eps_flip_min_change_pct

        if not tickers:
            return set()

        as_of = datetime.strptime(as_of_date, "%Y%m%d").date()
        start_date = as_of - timedelta(days=lookback_months * 31)

        try:
            import sqlalchemy as sa

            with storage.engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        "SELECT ticker, date, eps FROM fundamental "
                        "WHERE date BETWEEN :s AND :e "
                        "AND ticker IN :tickers "
                        "ORDER BY ticker, date"
                    ).bindparams(sa.bindparam("tickers", expanding=True)),
                    {
                        "s": start_date,
                        "e": as_of,
                        "tickers": tickers,
                    },
                ).fetchall()
        except Exception as e:
            logger.warning(f"EPS 부호 반전 조회 실패: {e}")
            return set()

        if not rows:
            return set()

        df = pd.DataFrame(rows, columns=["ticker", "date", "eps"])
        df = df.dropna(subset=["eps"])

        flipped: set[str] = set()
        for t, g in df.groupby("ticker"):
            eps_series = g["eps"].values
            if len(eps_series) < 2:
                continue
            # 부호 변화 탐지
            signs = np.sign(eps_series)
            sign_changes = np.where(np.diff(signs) != 0)[0]
            if len(sign_changes) == 0:
                continue
            # 각 부호 변화 시점의 변동률 검사
            for idx in sign_changes:
                prev_eps = eps_series[idx]
                curr_eps = eps_series[idx + 1]
                if prev_eps == 0:
                    continue
                change = abs((curr_eps - prev_eps) / prev_eps)
                if change >= min_change_pct:
                    flipped.add(t)
                    break

        if flipped:
            logger.info(
                f"EPS 부호 반전 필터: {len(flipped)}종목 감지 "
                f"(lookback={lookback_months}개월, 변동률>={min_change_pct*100:.0f}%)"
            )
        return flipped
