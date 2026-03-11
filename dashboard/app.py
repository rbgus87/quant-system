# dashboard/app.py
"""Streamlit 모니터링 대시보드

실행:
  streamlit run dashboard/app.py
"""

import sys
import os
import logging

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from trading.kiwoom_api import KiwoomRestClient

logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="멀티팩터 퀀트 대시보드",
    page_icon="📊",
    layout="wide",
)
st.title("한국 멀티팩터 퀀트 포트폴리오")
st.caption("밸류 x 모멘텀 x 퀄리티 | 매월 리밸런싱")

# ────────────────────────────────────────────
# 계좌 데이터 로드
# ────────────────────────────────────────────


@st.cache_resource
def _get_kiwoom_client() -> KiwoomRestClient:
    """KiwoomRestClient 싱글톤 (토큰 재사용)"""
    return KiwoomRestClient()


@st.cache_data(ttl=60)
def load_balance() -> dict:
    """키움 API에서 계좌 잔고 로드 (60초 캐시)

    Returns:
        잔고 dict
    """
    return _get_kiwoom_client().get_balance()


try:
    balance = load_balance()
    holdings = balance.get("holdings", [])
    total_value = balance.get("total_eval_amount", 0)
    cash = balance.get("cash", 0)
    total_profit = balance.get("total_profit", 0)

    # KPI 카드
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("총 평가금액", f"{total_value:,.0f}원")
    with col2:
        st.metric("예수금", f"{cash:,.0f}원")
    with col3:
        st.metric("보유 종목 수", f"{len(holdings)}개")
    with col4:
        st.metric("총 손익", f"{total_profit:,.0f}원")

except Exception as e:
    st.error(f"API 연결 실패: {e}")
    st.info("IS_PAPER_TRADING 설정과 IP 등록 여부를 확인하세요.")
    holdings = []

# ────────────────────────────────────────────
# 보유 종목 테이블
# ────────────────────────────────────────────
st.subheader("현재 포트폴리오")
if holdings:
    df = pd.DataFrame(holdings)
    df.columns = [
        c.replace("_", " ").title() if c != "ticker" else c for c in df.columns
    ]
    st.dataframe(df, use_container_width=True)
else:
    st.info("보유 종목 없음")

# ────────────────────────────────────────────
# 수익 곡선 (DB 히스토리 로드 시 활성화)
# ────────────────────────────────────────────
st.subheader("수익 곡선")
st.info("백테스트 또는 실전 운영 데이터를 DB에서 불러와 여기에 표시합니다.")
