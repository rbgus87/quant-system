# dart_notifier/filter.py
"""DART 공시 종류 분류 로직

config.yaml의 dart_notifier 설정 기반으로 즉시 알림 / 일일 요약을 구분한다.
pblntf_detail_ty(공시상세유형) 코드를 사용한다.
"""

import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# pblntf_detail_ty 코드 → 한글 명칭 (전체 매핑)
DISCLOSURE_TYPE_NAMES: dict[str, str] = {
    "A001": "사업보고서",
    "A002": "반기보고서",
    "A003": "분기보고서",
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


def _get_instant_codes() -> set[str]:
    """config에서 즉시 알림 대상 pblntf_detail_ty 코드 집합을 반환한다."""
    try:
        return set(settings.dart_notifier.get_instant_codes())
    except Exception:
        # 설정 로드 실패 시 기본값
        return {"B001", "B002", "B003", "E001", "E002",
                "G001", "G002", "G003", "G004", "H001", "H002", "H003"}


def classify_disclosure(pblntf_detail_ty: Optional[str]) -> str:
    """공시 유형 코드를 카테고리로 분류한다.

    Args:
        pblntf_detail_ty: DART 공시상세유형 코드 (예: "B001")

    Returns:
        "instant" | "daily_summary"
    """
    if not pblntf_detail_ty:
        return "daily_summary"

    instant_codes = _get_instant_codes()
    if pblntf_detail_ty in instant_codes:
        return "instant"

    return "daily_summary"


def get_disclosure_type_name(pblntf_detail_ty: Optional[str]) -> str:
    """공시 유형 코드의 한글 명칭을 반환한다."""
    if not pblntf_detail_ty:
        return "기타공시"
    return DISCLOSURE_TYPE_NAMES.get(pblntf_detail_ty, "기타공시")
