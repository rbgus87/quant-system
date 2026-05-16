# config/font.py
"""matplotlib 한글 폰트 설정 유틸리티"""

import logging
import platform
from typing import Optional

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = {
    "Windows": ["Malgun Gothic", "맑은 고딕"],
    "Darwin": ["AppleGothic", "Apple SD Gothic Neo"],
    "Linux": ["NanumGothic", "NanumBarunGothic", "UnDotum"],
}


def get_korean_font_name() -> Optional[str]:
    """현재 OS에서 사용 가능한 한글 폰트 이름을 반환한다.

    Returns:
        폰트 이름 또는 None (한글 폰트를 찾지 못한 경우)
    """
    try:
        import matplotlib.font_manager as fm
    except ImportError:
        return None

    system = platform.system()
    candidates = _FONT_CANDIDATES.get(system, ["NanumGothic"])
    available_fonts = {f.name for f in fm.fontManager.ttflist}

    for font_name in candidates:
        if font_name in available_fonts:
            return font_name

    logger.warning(
        "한글 폰트를 찾을 수 없습니다 (시도: %s). "
        "차트에서 한글이 깨질 수 있습니다.",
        candidates,
    )
    return None


def setup_matplotlib_korean_font() -> None:
    """matplotlib rcParams에 한글 폰트를 설정한다."""
    try:
        import matplotlib
        import matplotlib.font_manager as fm
    except ImportError:
        logger.debug("matplotlib 미설치, 폰트 설정 건너뜀")
        return

    font_name = get_korean_font_name()
    if font_name:
        matplotlib.rcParams["font.family"] = font_name
        matplotlib.rcParams["axes.unicode_minus"] = False
        # PyInstaller exe 환경에서 폰트 캐시 불일치 방지
        fm._load_fontmanager(try_read_cache=False)
        logger.info("matplotlib 한글 폰트 설정: %s", font_name)
    else:
        # 폴백: Windows 기본 한글 폰트 강제 시도
        matplotlib.rcParams["font.family"] = "Malgun Gothic"
        matplotlib.rcParams["axes.unicode_minus"] = False
        logger.warning("한글 폰트 자동 감지 실패, Malgun Gothic 강제 설정")
