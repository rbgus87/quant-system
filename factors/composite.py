# factors/composite.py
import pandas as pd
import logging
from typing import Optional
from config.settings import settings
from factors.utils import weighted_average_nan_safe

logger = logging.getLogger(__name__)

# 금융주 섹터명 (WICS + KRX 표준 통합)
# KRX/pykrx/FDR 섹터 API 모두 차단(2025-12-27) → 종목명 휴리스틱 매칭 사용
FINANCE_SECTORS = {
    "은행", "증권", "보험", "기타금융", "다각화된금융",
    # KRX/FDR 표준 업종명 확장
    "금융업", "금융", "보험업", "기타 금융",
}

# 종목명 휴리스틱 매칭용 — (키워드, 섹터명)
# 우선순위 순서 (먼저 매칭되는 게 채택)
FINANCIAL_NAME_PATTERNS: list[tuple[str, str]] = [
    ("금융지주", "금융업"),
    ("은행", "은행"),
    ("증권", "증권"),
    ("손해보험", "보험"),
    ("화재해상", "보험"),
    ("화재", "보험"),
    ("해상", "보험"),
    ("생명", "보험"),
    ("보험", "보험"),
    ("카드", "금융업"),
    ("캐피탈", "금융업"),
    ("파이낸셜", "금융업"),
]

# 휴리스틱 키워드만으로 잡히지 않는 명시적 금융주 화이트리스트
# (종목명에 "은행"/"증권" 등 키워드가 없는 케이스)
FINANCIAL_TICKER_WHITELIST: dict[str, str] = {
    "055550": "금융업",  # 신한지주 (키워드 매칭 안 됨)
    "086790": "금융업",  # 하나금융지주 (지주 매칭 보조)
    "138930": "금융업",  # BNK금융지주
    "175330": "금융업",  # JB금융지주
    "139130": "금융업",  # DGB금융지주
    "316140": "금융업",  # 우리금융지주
    "323410": "은행",    # 카카오뱅크
}


# KSIC 상위 2자리 → 투자용 섹터 매핑 (15~20개)
# 출처: 통계청 한국표준산업분류 (KSIC) 중분류 + DART induty_code
KSIC_TO_SECTOR: dict[str, str] = {
    # 1차 산업
    "01": "농림·어업", "02": "농림·어업", "03": "농림·어업",
    # 광업
    "05": "광업", "06": "광업", "07": "광업", "08": "광업",
    # 음식료
    "10": "식품", "11": "음료", "12": "담배",
    # 섬유·의류·가죽
    "13": "섬유·의류", "14": "섬유·의류", "15": "가죽·신발",
    # 제지·인쇄
    "17": "제지·목재", "18": "인쇄",
    # 석유·화학·의약
    "19": "석유·화학", "20": "석유·화학", "21": "의약품",
    # 고무·플라스틱·비금속
    "22": "고무·플라스틱", "23": "비금속광물",
    # 금속
    "24": "철강·금속", "25": "철강·금속",
    # 전자·전기·기계
    "26": "전자·IT", "27": "전자·IT", "28": "전기장비",
    "29": "기계", "30": "자동차",
    "31": "운송장비", "32": "가구·기타", "33": "기타 제조",
    # 에너지
    "35": "에너지·유틸리티", "36": "에너지·유틸리티",
    "37": "에너지·유틸리티", "38": "에너지·유틸리티",
    "39": "에너지·유틸리티",
    # 건설
    "41": "건설", "42": "건설", "43": "건설",
    # 유통
    "45": "유통", "46": "유통", "47": "유통",
    # 운수·물류
    "49": "운수·물류", "50": "운수·물류",
    "51": "운수·물류", "52": "운수·물류",
    # 숙박·음식
    "55": "숙박·음식", "56": "숙박·음식",
    # 미디어·통신
    "58": "출판·미디어", "59": "출판·미디어",
    "60": "방송·통신", "61": "방송·통신",
    # IT 서비스
    "62": "IT서비스", "63": "IT서비스",
    # 금융 (전부 is_financial)
    "64": "금융업", "65": "보험", "66": "금융업",
    # 부동산·전문서비스
    "68": "부동산",
    "70": "전문서비스", "71": "전문서비스", "72": "전문서비스",
    "73": "전문서비스", "74": "전문서비스", "75": "전문서비스",
    # 공공
    "84": "공공행정", "85": "교육",
    # 보건·의료
    "86": "보건·의료", "87": "보건·의료",
    # 예술·여가
    "90": "예술·엔터", "91": "예술·엔터",
}

# KSIC 매핑 결과가 금융주로 판정되는 섹터명
KSIC_FINANCIAL_SECTORS: set[str] = {"금융업", "보험"}


def classify_by_ksic(induty_code: str) -> tuple[str | None, bool]:
    """DART induty_code → (sector_name, is_financial) 변환.

    induty_code는 KSIC 5자리(또는 3자리). 상위 2자리로 매핑.
    매핑 실패 시 ("기타", False) 반환.

    Args:
        induty_code: DART 기업개황 induty_code 필드 (예: "26410", "64992")

    Returns:
        (sector_name, is_financial)
        매핑 안 되면 ("기타", False)
        induty_code 빈 값이면 (None, False)
    """
    if not induty_code:
        return None, False

    code = str(induty_code).strip()
    if not code:
        return None, False

    # 상위 2자리 (3자리 코드면 zfill 처리)
    prefix = code[:2] if len(code) >= 2 else code.zfill(2)
    sector = KSIC_TO_SECTOR.get(prefix)
    if sector is None:
        return "기타", False
    is_fin = sector in KSIC_FINANCIAL_SECTORS
    return sector, is_fin


def classify_financial_by_name(
    ticker: str, name: str,
) -> tuple[bool, str | None]:
    """종목코드+이름 기반 금융주 판정 (휴리스틱).

    KRX/pykrx 섹터 API 차단 환경에서 종목명 키워드 매칭으로 금융주 식별.
    KOSPI 금융주 약 50개 종목에 대해 ~95% 정확도 추정.

    Args:
        ticker: 종목코드
        name: 종목명 (예: "신한지주", "삼성생명")

    Returns:
        (is_financial, sector_name) — sector_name None이면 비금융
    """
    # 1. 화이트리스트 우선 (키워드 매칭 미스 보완)
    if ticker in FINANCIAL_TICKER_WHITELIST:
        return True, FINANCIAL_TICKER_WHITELIST[ticker]

    # 2. 종목명 키워드 매칭
    if not name:
        return False, None
    for keyword, sector in FINANCIAL_NAME_PATTERNS:
        if keyword in name:
            return True, sector

    return False, None


class MultiFactorComposite:
    """멀티팩터 스코어 합산 및 최종 종목 선정

    밸류 40% + 모멘텀 40% + 퀄리티 20%
    """

    def __init__(self) -> None:
        self.w = settings.factor_weights
        logger.info(
            "팩터 가중치 — 밸류:%.2f, 모멘텀:%.2f, 퀄리티:%.2f, 저변동성:%.2f",
            self.w.value, self.w.momentum, self.w.quality, self.w.low_vol,
        )

    def calculate(
        self,
        value_score: pd.Series,
        momentum_score: pd.Series,
        quality_score: pd.Series,
        low_vol_score: Optional[pd.Series] = None,
        min_factor_count: int = 2,
    ) -> pd.DataFrame:
        """3~4개 팩터 가중 합산.

        2개 이상 팩터가 있는 종목은 가용 가중치 정규화로 포함.
        low_vol_score: None 또는 self.w.low_vol == 0이면 무시 (하위 호환).

        Args:
            value_score: 밸류 스코어 (0~100)
            momentum_score: 모멘텀 스코어 (0~100)
            quality_score: 퀄리티 스코어 (0~100)
            low_vol_score: 저변동성 스코어 (0~100), None이면 비활성
            min_factor_count: 최소 필요 팩터 수 (기본 2)

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score,
            quality_score, low_vol_score, composite_score]) composite_score 내림차순
        """
        _use_low_vol = low_vol_score is not None and self.w.low_vol > 0

        _low_vol_idx = set(low_vol_score.index) if _use_low_vol else set()
        all_tickers = sorted(
            set(value_score.index)
            | set(momentum_score.index)
            | set(quality_score.index)
            | _low_vol_idx
        )

        _empty_cols = ["value_score", "momentum_score", "quality_score", "low_vol_score", "composite_score"]
        if not all_tickers:
            logger.warning("유효 종목 없음 — 빈 결과 반환")
            return pd.DataFrame(columns=_empty_cols)

        data: dict[str, pd.Series] = {
            "value_score":    value_score.reindex(all_tickers),
            "momentum_score": momentum_score.reindex(all_tickers),
            "quality_score":  quality_score.reindex(all_tickers),
            "low_vol_score":  (
                low_vol_score.reindex(all_tickers)
                if _use_low_vol
                else pd.Series(float("nan"), index=all_tickers)
            ),
        }
        df = pd.DataFrame(data)

        # 최소 팩터 수 계산에는 활성 팩터만 포함
        active_factor_cols = ["value_score", "momentum_score", "quality_score"]
        if _use_low_vol:
            active_factor_cols.append("low_vol_score")

        factor_count = df[active_factor_cols].notna().sum(axis=1)
        df = df[factor_count >= min_factor_count].copy()

        if df.empty:
            logger.warning(f"최소 {min_factor_count}개 팩터 충족 종목 없음")
            return pd.DataFrame(columns=_empty_cols)

        n_full = (factor_count.reindex(df.index) == len(active_factor_cols)).sum()
        n_partial = len(df) - n_full
        logger.info(
            f"팩터 종목: {len(df)}개 (완전 {n_full}개, 부분 {n_partial}개)"
        )

        # 가중 합산 (NaN 팩터 가중치 재분배)
        score_parts: dict[str, tuple[pd.Series, float]] = {
            "value":    (df["value_score"],    self.w.value),
            "momentum": (df["momentum_score"], self.w.momentum),
            "quality":  (df["quality_score"],  self.w.quality),
        }
        if _use_low_vol:
            score_parts["low_vol"] = (df["low_vol_score"], self.w.low_vol)

        df["composite_score"] = weighted_average_nan_safe(score_parts)
        df = df.dropna(subset=["composite_score"])
        return df.sort_values("composite_score", ascending=False)

    def apply_universe_filter(
        self,
        composite_df: pd.DataFrame,
        market_cap: pd.Series,
        finance_tickers: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """유니버스 필터 적용

        Args:
            composite_df: 복합 스코어 DataFrame
            market_cap: 시가총액 Series (index=ticker)
            finance_tickers: 금융주 종목 코드 리스트

        Returns:
            필터 적용된 DataFrame
        """
        result = composite_df.copy()

        # 시가총액 하위 N% 제외
        if not market_cap.empty:
            threshold = market_cap.quantile(
                settings.universe.min_market_cap_percentile / 100
            )
            valid = market_cap[market_cap >= threshold].index
            before = len(result)
            result = result[result.index.isin(valid)]
            logger.info(f"시가총액 필터: {before} → {len(result)}개")

        # 금융주 제외
        if finance_tickers:
            before = len(result)
            result = result[~result.index.isin(finance_tickers)]
            logger.info(f"금융주 제외: {before} → {len(result)}개")

        return result

    def select_top(
        self,
        composite_df: pd.DataFrame,
        n: Optional[int] = None,
    ) -> pd.DataFrame:
        """상위 N개 종목 선정 (동일 비중)

        Args:
            composite_df: 복합 스코어 DataFrame (composite_score 내림차순)
            n: 선정 종목 수 (기본: settings.portfolio.n_stocks)

        Returns:
            DataFrame with weight 컬럼 추가
        """
        n = n or settings.portfolio.n_stocks
        selected = composite_df.head(n).copy()

        if len(selected) > 0:
            selected["weight"] = 1.0 / len(selected)

        logger.info(
            f"포트폴리오 구성 완료: {len(selected)}개 종목 | "
            f"평균 복합스코어: {selected['composite_score'].mean():.1f}"
        )
        return selected
