# dart_notifier/filter.py
"""DART 공시 종류 분류 로직

pblntf_detail_ty(공시상세유형) 코드 기반으로 즉시 알림 / 일일 요약을 구분한다.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 즉시 알림 대상 공시 유형 코드 (pblntf_detail_ty)
# https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001
INSTANT_TYPES: dict[str, str] = {
    "B001": "주요사항보고서",
    "B002": "주요경영사항신고",
    "B003": "최대주주변경",
    "E001": "불성실공시법인지정",
    "E002": "공정공시",
    "E003": "시장조치·안내",
    "G001": "전환사채등발행",
    "G002": "신주인수권부사채발행",
    "G003": "유상증자",
    "G004": "무상증자",
    "H001": "합병",
    "H002": "분할",
    "H003": "분할합병",
    "I001": "주식교환·이전",
    "I002": "자기주식취득·처분",
}

# 일일 요약 대상 (정기공시)
DAILY_SUMMARY_TYPES: dict[str, str] = {
    "A001": "사업보고서",
    "A002": "반기보고서",
    "A003": "분기보고서",
}


def classify_disclosure(pblntf_detail_ty: Optional[str]) -> str:
    """공시 유형 코드를 카테고리로 분류한다.

    Args:
        pblntf_detail_ty: DART 공시상세유형 코드 (예: "B001")

    Returns:
        "instant" | "daily_summary" | "skip"
    """
    if not pblntf_detail_ty:
        return "daily_summary"

    if pblntf_detail_ty in INSTANT_TYPES:
        return "instant"
    if pblntf_detail_ty in DAILY_SUMMARY_TYPES:
        return "daily_summary"

    # 알 수 없는 유형 → 일일 요약에 포함
    return "daily_summary"


def get_disclosure_type_name(pblntf_detail_ty: Optional[str]) -> str:
    """공시 유형 코드의 한글 명칭을 반환한다."""
    if not pblntf_detail_ty:
        return "기타공시"
    return (
        INSTANT_TYPES.get(pblntf_detail_ty)
        or DAILY_SUMMARY_TYPES.get(pblntf_detail_ty)
        or "기타공시"
    )
