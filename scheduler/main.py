# scheduler/main.py
"""자동매매 스케줄러 (APScheduler 3.x 기반)

실행:
  python scheduler/main.py
  python scheduler/main.py --dry-run

스케줄:
  - 매 영업일 08:50  → 월말이면 리밸런싱 신호 계산 실행
  - 매 영업일 15:35  → 일별 수익 리포트 발송
"""

import argparse
import logging
import os
import sys
from datetime import datetime

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from config.calendar import is_krx_business_day, is_last_krx_business_day_of_month
from trading.kiwoom_api import KiwoomRestClient
from trading.order import OrderExecutor
from notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 유틸리티 (KRX 캘린더 기반 — 한국 공휴일 인식)
# ────────────────────────────────────────────


def is_business_day() -> bool:
    """오늘이 KRX 거래일인지 확인 (한국 공휴일 인식)

    Returns:
        True=거래일, False=휴장일(공휴일/주말)
    """
    return is_krx_business_day()


def is_last_business_day_of_month() -> bool:
    """오늘이 이번 달 마지막 KRX 거래일인지 확인 (한국 공휴일 인식)

    Returns:
        True=마지막 거래일, False=아님
    """
    return is_last_krx_business_day_of_month()


# ────────────────────────────────────────────
# 작업 함수
# ────────────────────────────────────────────


def run_monthly_rebalancing() -> None:
    """월말 리밸런싱 실행

    - 영업일이 아니거나 월말이 아니면 스킵
    - 리밸런싱 실패 시 텔레그램 에러 알림
    """
    if not is_business_day() or not is_last_business_day_of_month():
        return

    logger.info("=" * 50)
    logger.info("월말 리밸런싱 시작")
    notifier = TelegramNotifier()
    notifier.send("월말 리밸런싱을 시작합니다...")

    try:
        from strategy.screener import MultiFactorScreener

        api = KiwoomRestClient()
        screener = MultiFactorScreener()
        executor = OrderExecutor()

        # 새 포트폴리오 계산
        today_str = datetime.now().strftime("%Y%m%d")
        portfolio_df = screener.screen(today_str)
        new_portfolio = portfolio_df.index.tolist() if not portfolio_df.empty else []
        logger.info(f"신규 포트폴리오: {len(new_portfolio)}개 종목")

        if not new_portfolio:
            notifier.send("스크리닝 결과가 비어 있어 리밸런싱을 건너뜁니다.")
            return

        # 현재 보유 종목 조회
        balance = api.get_balance()
        current_holdings = [h["ticker"] for h in balance["holdings"] if h["qty"] > 0]
        total_value = balance.get("total_eval_amount", 0)

        # 리밸런싱 주문 실행
        sell_done, buy_done = executor.execute_rebalancing(
            current_holdings, new_portfolio
        )

        # 결과 알림
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


def run_daily_report() -> None:
    """장 마감 후 일별 수익 리포트 발송"""
    if not is_business_day():
        return

    notifier = TelegramNotifier()
    try:
        api = KiwoomRestClient()
        balance = api.get_balance()
        total_value = balance.get("total_eval_amount", 0)

        notifier.send(
            f"*일별 리포트*\n\n"
            f"총 평가금액: `{total_value:,.0f}원`\n"
            f"보유 종목: `{len(balance['holdings'])}개`"
        )
    except Exception as e:
        logger.error(f"일별 리포트 오류: {e}")
        notifier.send_error(str(e))


# ────────────────────────────────────────────
# 스케줄러 설정 및 실행
# ────────────────────────────────────────────


def main() -> None:
    """스케줄러 메인 엔트리포인트"""
    parser = argparse.ArgumentParser(description="퀀트 자동매매 스케줄러")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="스케줄러 시작 없이 설정만 확인",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not settings.is_paper_trading:
        logger.warning("실전투자 모드입니다! 신중하게 진행하세요")

    if args.dry_run:
        logger.info("[DRY-RUN] 스케줄러 설정 확인 완료")
        logger.info(f"  모의투자: {settings.is_paper_trading}")
        logger.info(
            f"  텔레그램: {'설정됨' if settings.telegram_bot_token else '미설정'}"
        )
        logger.info(f"  키움 API: {'설정됨' if settings.kiwoom_app_key else '미설정'}")
        return

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        run_monthly_rebalancing,
        trigger="cron",
        day_of_week="mon-fri",
        hour=8,
        minute=50,
        id="monthly_rebalancing",
    )

    scheduler.add_job(
        run_daily_report,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=35,
        id="daily_report",
    )

    logger.info("스케줄러 시작 (Ctrl+C로 종료)")
    TelegramNotifier().send("퀀트 스케줄러가 시작되었습니다.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
