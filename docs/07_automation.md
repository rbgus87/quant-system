# 07. 자동화 — 스케줄러 · 텔레그램 · 대시보드

## 7-1. notify/telegram.py

```python
# notify/telegram.py
"""
텔레그램 알림 모듈

python-telegram-bot v21은 완전 async 기반이지만
스케줄러 내에서 간단히 쓰려면 requests 직접 호출이 더 단순.
→ requests 방식 채택 (라이브러리 의존성 불필요)
"""
import requests
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """텔레그램 봇 메시지 발송"""

    def __init__(self):
        self.token   = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """
        메시지 발송

        Returns:
            True: 성공, False: 실패
        """
        if not self.token or not self.chat_id:
            logger.warning("텔레그램 설정 없음 (.env 확인)")
            return False

        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {
            "chat_id":    self.chat_id,
            "text":       message,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug("텔레그램 발송 성공")
                return True
            else:
                logger.error(f"텔레그램 발송 실패 ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            logger.error(f"텔레그램 오류: {e}")
            return False

    def send_rebalancing_report(
        self,
        sell_done: list[str],
        buy_done: list[str],
        total_value: float,
        sell_total: int = 0,
        buy_total: int = 0,
    ) -> bool:
        """월별 리밸런싱 결과 알림"""
        sell_preview = ", ".join(sell_done[:5])
        if len(sell_done) > 5:
            sell_preview += " ..."
        buy_preview = ", ".join(buy_done[:5])
        if len(buy_done) > 5:
            buy_preview += " ..."

        msg = (
            f"📊 *월별 리밸런싱 완료*\n\n"
            f"🔴 매도: {len(sell_done)}/{sell_total or len(sell_done)}개\n"
            f"`{sell_preview}`\n\n"
            f"🟢 매수: {len(buy_done)}/{buy_total or len(buy_done)}개\n"
            f"`{buy_preview}`\n\n"
            f"💰 총 평가금액: {total_value:,.0f}원"
        )
        return self.send(msg)

    def send_daily_report(self, daily_return: float, total_value: float) -> bool:
        """일별 수익 리포트"""
        emoji = "📈" if daily_return >= 0 else "📉"
        msg = (
            f"{emoji} *일별 리포트*\n\n"
            f"당일 수익률: `{daily_return * 100:+.2f}%`\n"
            f"총 평가금액: `{total_value:,.0f}원`"
        )
        return self.send(msg)

    def send_error(self, error_message: str) -> bool:
        """오류 알림"""
        msg = f"❌ *오류 발생*\n\n```\n{error_message[:500]}\n```"
        return self.send(msg)
```

---

## 7-2. scheduler/main.py

```python
# scheduler/main.py
"""
자동매매 스케줄러 (APScheduler 3.x 기반)

실행:
  python scheduler/main.py

스케줄:
  - 매 영업일 08:50  → 월말이면 리밸런싱 신호 계산 실행
  - 매 영업일 15:35  → 일별 수익 리포트 발송
"""
import logging
from datetime import datetime

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler

from config.logging_config import setup_logging
from config.settings import settings
from trading.kiwoom_api import KiwoomRestClient
from trading.order import OrderExecutor
from notify.telegram import TelegramNotifier

setup_logging()
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 유틸리티
# ────────────────────────────────────────────

def is_business_day() -> bool:
    """오늘이 주중 영업일인지 확인 (공휴일 미처리 → 추후 workalendar 도입 가능)"""
    return datetime.now().weekday() < 5  # 0=월 ~ 4=금


def is_last_business_day_of_month() -> bool:
    """
    오늘이 이번 달 마지막 영업일인지 확인

    pd.offsets.BMonthEnd(0): 현재 날짜를 기준으로 이번 달 마지막 영업일 반환
    (당일이 영업일이면 당일, 아니면 직전 영업일)
    """
    today = pd.Timestamp.today().normalize()
    last_bday = today + pd.offsets.BMonthEnd(0)
    return today == last_bday


# ────────────────────────────────────────────
# 작업 함수
# ────────────────────────────────────────────

def run_monthly_rebalancing():
    """
    월말 리밸런싱 실행
    - 영업일이 아니거나 월말이 아니면 스킵
    - 신호 계산 시점: T (월말 영업일)
    - 주문 실행 시점: T+1 시가 (실제 주문 → 다음날 시장가)
    """
    if not is_business_day() or not is_last_business_day_of_month():
        return

    logger.info("=" * 50)
    logger.info("월말 리밸런싱 시작")
    notifier = TelegramNotifier()
    notifier.send("🔄 월말 리밸런싱을 시작합니다...")

    try:
        from strategy.screener import MultiFactorScreener   # 순환 임포트 방지

        api = KiwoomRestClient()
        screener = MultiFactorScreener()
        executor = OrderExecutor()

        # ① 새 포트폴리오 계산
        today_str = datetime.now().strftime("%Y%m%d")
        new_portfolio = screener.get_portfolio(today_str)
        logger.info(f"신규 포트폴리오: {len(new_portfolio)}개 종목")

        # ② 현재 보유 종목 조회
        balance = api.get_balance()
        current_holdings = [h["ticker"] for h in balance["holdings"] if h["qty"] > 0]
        total_value = balance.get("total_eval_amount", 0)

        # ③ 리밸런싱 주문 실행
        sell_done, buy_done = executor.execute_rebalancing(current_holdings, new_portfolio)

        # ④ 결과 알림
        updated_balance = api.get_balance()
        notifier.send_rebalancing_report(
            sell_done=sell_done,
            buy_done=buy_done,
            total_value=updated_balance.get("total_eval_amount", total_value),
            sell_total=len([t for t in current_holdings if t not in new_portfolio]),
            buy_total=len([t for t in new_portfolio if t not in current_holdings]),
        )
        logger.info("월말 리밸런싱 완료")

    except Exception as e:
        logger.error(f"리밸런싱 오류: {e}", exc_info=True)
        notifier.send_error(str(e))


def run_daily_report():
    """장 마감 후 일별 수익 리포트 발송"""
    if not is_business_day():
        return

    notifier = TelegramNotifier()
    try:
        api = KiwoomRestClient()   # ← KISApiClient 아님 (수정됨)
        balance = api.get_balance()
        total_value = balance.get("total_eval_amount", 0)

        # 단순 수익률은 DB에서 어제 총자산과 비교해야 정확
        # 여기서는 간단히 오늘 총자산만 리포트
        notifier.send(
            f"📊 *일별 리포트*\n\n"
            f"총 평가금액: `{total_value:,.0f}원`\n"
            f"보유 종목: `{len(balance['holdings'])}개`"
        )
    except Exception as e:
        logger.error(f"일별 리포트 오류: {e}")
        notifier.send_error(str(e))


# ────────────────────────────────────────────
# 스케줄러 설정 및 실행
# ────────────────────────────────────────────

if __name__ == "__main__":
    # ⚠️ IS_PAPER_TRADING 확인
    if not settings.is_paper_trading:
        logger.warning("⚠️⚠️⚠️  실전투자 모드입니다! 신중하게 진행하세요  ⚠️⚠️⚠️")

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 매 영업일 08:50 — 리밸런싱 신호 확인 (월말이면 실행)
    scheduler.add_job(
        run_monthly_rebalancing,
        trigger="cron",
        day_of_week="mon-fri",
        hour=8,
        minute=50,
        id="monthly_rebalancing",
    )

    # 매 영업일 15:35 — 일별 리포트 (장 마감 15:30 이후)
    scheduler.add_job(
        run_daily_report,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=35,
        id="daily_report",
    )

    logger.info("스케줄러 시작 (Ctrl+C로 종료)")
    TelegramNotifier().send("🚀 퀀트 스케줄러가 시작되었습니다.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
```

---

## 7-3. strategy/screener.py (스케줄러에서 사용)

```python
# strategy/screener.py
import logging
from data.collector import KRXDataCollector, ReturnCalculator
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite
from config.settings import settings

logger = logging.getLogger(__name__)


class MultiFactorScreener:
    """팩터 계산 → 포트폴리오 선정 통합 클래스"""

    def __init__(self):
        self.krx = KRXDataCollector()
        self.ret_calc = ReturnCalculator()
        self.value_f = ValueFactor()
        self.momentum_f = MomentumFactor()
        self.quality_f = QualityFactor()
        self.composite = MultiFactorComposite()

    def get_portfolio(
        self,
        date_str: str,
        market: str = "KOSPI",
        n: int | None = None,
    ) -> list[str]:
        """
        특정 날짜 기준 포트폴리오 종목 코드 반환

        Args:
            date_str: 기준 날짜 (YYYYMMDD)
            market:   대상 시장
            n:        편입 종목 수 (None이면 settings에서)

        Returns:
            종목 코드 리스트
        """
        logger.info(f"포트폴리오 계산 시작: {date_str}")

        fundamentals = self.krx.get_fundamentals_all(date_str, market)
        if fundamentals.empty:
            logger.error("기본 지표 데이터 없음")
            return []

        market_cap_df = self.krx.get_market_cap(date_str, market)
        market_cap = (
            market_cap_df["market_cap"]
            if "market_cap" in market_cap_df.columns
            else pd.Series(dtype=float)
        )

        tickers = fundamentals.index.tolist()
        returns_12m = self.ret_calc.get_returns_for_universe(tickers, date_str, 12, 1)
        returns_3m  = self.ret_calc.get_returns_for_universe(tickers, date_str, 3, 1)

        value_s    = self.value_f.calculate(fundamentals)
        momentum_s = self.momentum_f.calculate(returns_12m, returns_3m=returns_3m)
        quality_s  = self.quality_f.calculate(fundamentals)

        composite_df = self.composite.calculate(value_s, momentum_s, quality_s)
        filtered_df  = self.composite.apply_universe_filter(composite_df, market_cap)
        selected     = self.composite.select_top(filtered_df, n=n)

        result = selected.index.tolist()
        logger.info(f"포트폴리오 선정 완료: {len(result)}개 종목")
        return result
```

---

## 7-4. dashboard/app.py

```python
# dashboard/app.py
"""
Streamlit 모니터링 대시보드

실행:
  streamlit run dashboard/app.py
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from trading.kiwoom_api import KiwoomRestClient    # ← KISApiClient 아님 (수정됨)

setup_logging()

st.set_page_config(
    page_title="멀티팩터 퀀트 대시보드",
    page_icon="📊",
    layout="wide",
)
st.title("📊 한국 멀티팩터 퀀트 포트폴리오")
st.caption("밸류 × 모멘텀 × 퀄리티 | 매월 리밸런싱")

# ────────────────────────────────────────────
# 계좌 데이터 로드
# ────────────────────────────────────────────
@st.cache_data(ttl=60)   # 60초 캐시
def load_balance():
    client = KiwoomRestClient()
    return client.get_balance()

try:
    balance = load_balance()
    holdings = balance.get("holdings", [])
    total_value = balance.get("total_eval_amount", 0)
    cash = balance.get("cash", 0)

    # ─── KPI 카드
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("총 평가금액", f"₩{total_value:,.0f}")
    with col2:
        st.metric("예수금", f"₩{cash:,.0f}")
    with col3:
        st.metric("보유 종목 수", f"{len(holdings)}개")
    with col4:
        total_profit = balance.get("total_profit", 0)
        st.metric("총 손익", f"₩{total_profit:,.0f}")

except Exception as e:
    st.error(f"API 연결 실패: {e}")
    st.info("IS_PAPER_TRADING 설정과 IP 등록 여부를 확인하세요.")
    holdings = []

# ────────────────────────────────────────────
# 보유 종목 테이블
# ────────────────────────────────────────────
st.subheader("📋 현재 포트폴리오")
if holdings:
    df = pd.DataFrame(holdings)
    df["profit_rate"] = df["profit_rate"].map(lambda x: f"{x:+.2f}%")
    df["eval_amount"] = df["eval_amount"].map(lambda x: f"₩{x:,.0f}")
    st.dataframe(
        df[["ticker", "name", "qty", "avg_price", "current_price", "profit_rate", "eval_amount"]],
        use_container_width=True,
    )
else:
    st.info("보유 종목 없음")

# ────────────────────────────────────────────
# 수익 곡선 (DB에서 히스토리 로드 시 활성화)
# ────────────────────────────────────────────
st.subheader("📈 수익 곡선")
st.info("백테스트 또는 실전 운영 데이터를 DB에서 불러와 여기에 표시하세요.")
# 실제 구현 시:
# from data.storage import PortfolioHistory
# history = PortfolioHistory().load()
# fig = px.line(history, x="date", y="portfolio_value")
# st.plotly_chart(fig, use_container_width=True)
```

---

## 7-5. 수정된 버그 목록

| 위치 | 원본 버그 | 수정 내용 |
|------|----------|----------|
| `scheduler/main.py` | `run_daily_report`에서 `KISApiClient()` 사용 | `KiwoomRestClient()`로 수정 |
| `scheduler/main.py` | `schedule` 라이브러리 사용 (requirements에 `APScheduler`) | `APScheduler BlockingScheduler`로 통일 |
| `scheduler/main.py` | `BMonthEnd(0)` 임포트 경로 불명확 | `pd.offsets.BMonthEnd(0)`으로 수정 |
| `scheduler/main.py` | `timezone` 미설정 | `timezone="Asia/Seoul"` 명시 |
| `dashboard/app.py` | `KISApiClient` 참조 | `KiwoomRestClient`로 수정 |
| `dashboard/app.py` | `sys.path` 설정 없이 내부 모듈 임포트 | `sys.path.insert()` 추가 |
