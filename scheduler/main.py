# scheduler/main.py
"""자동매매 스케줄러 (APScheduler 3.x 기반)

실행:
  python scheduler/main.py
  python scheduler/main.py --dry-run

스케줄:
  - 매 영업일 08:50  → 월말이면 리밸런싱 신호 계산 실행
  - 매 영업일 15:15  → 일별 방어 체크 (MDD 서킷브레이커 + 트레일링 스톱)
  - 매 영업일 15:35  → 일별 수익 리포트 발송
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from config.calendar import is_krx_business_day, is_last_krx_business_day_of_month
from data.storage import DataStorage
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


def _calc_vol_target_scale(api: KiwoomRestClient) -> float:
    """변동성 타겟팅 — KODEX 200 실현 변동성 기반 투자 비중 산출

    백테스트의 _calc_vol_target_scale과 동일 로직:
    실현 변동성 > 목표 → 비중 축소 (최소 20%)

    Args:
        api: KiwoomRestClient 인스턴스

    Returns:
        투자 비중 배율 (0.2 ~ 1.0)
    """
    import numpy as np

    vol_target = settings.trading.vol_target
    lookback = settings.trading.vol_lookback_days

    if vol_target <= 0:
        return 1.0

    try:
        from data.collector import KRXDataCollector

        collector = KRXDataCollector()
        end_dt = datetime.now()
        # 영업일 기준 lookback * 1.5 만큼 과거 데이터 확보
        start_dt = end_dt - timedelta(days=int(lookback * 1.5))
        start_str = start_dt.strftime("%Y%m%d")
        end_str = end_dt.strftime("%Y%m%d")

        df = collector.get_ohlcv("069500", start_str, end_str)  # KODEX 200
        if df is None or df.empty or len(df) < max(lookback, 20):
            logger.info("변동성 타겟팅: 데이터 부족 → 비중 1.0")
            return 1.0

        closes = df["close"].iloc[-lookback:]
        returns = closes.pct_change().dropna()
        if len(returns) < 10:
            return 1.0

        realized_vol = float(np.std(returns)) * np.sqrt(252)
        if realized_vol <= 0:
            return 1.0

        scale = vol_target / realized_vol
        result = min(1.0, max(0.2, scale))
        logger.info(
            f"변동성 타겟팅: 실현={realized_vol:.1%}, "
            f"목표={vol_target:.1%} → 비중 {result:.0%}"
        )
        return result

    except Exception as e:
        logger.warning(f"변동성 타겟팅 실패: {e} → 비중 1.0")
        return 1.0


def _execute_rebalancing_core(notifier: TelegramNotifier) -> None:
    """리밸런싱 공통 로직

    서킷브레이커 재진입 확인 → 스크리닝 → DB 저장 → 잔고 조회 →
    시장 레짐/변동성 타겟팅 반영 → 주문 실행 → 결과 알림

    Args:
        notifier: 텔레그램 알림 발송기
    """
    from strategy.screener import MultiFactorScreener

    # 현재 잔고 조회 후 OrderExecutor에 총 평가금액 전달 (MDD 기준값)
    api = KiwoomRestClient()
    balance = api.get_balance()

    # 잔고 API 실패 감지: 총평가·현금 모두 0이면 비정상 → 리밸런싱 중단
    if balance.get("total_eval_amount", 0) == 0 and balance.get("cash", 0) == 0:
        logger.error(
            "잔고 API 비정상 응답 (total_eval_amount=0, cash=0). "
            "리밸런싱을 중단합니다."
        )
        notifier.send_error("잔고 API 비정상 응답으로 리밸런싱을 중단합니다.")
        return

    current_holdings = [h["ticker"] for h in balance["holdings"] if h["qty"] > 0]
    total_value = balance.get("total_eval_amount", 0)

    # 고정 금액 모드: max_investment_amount > 0이면 투자 금액 제한
    max_inv = settings.portfolio.max_investment_amount
    if max_inv > 0 and total_value > max_inv:
        logger.info(
            f"고정 금액 모드: 계좌 {total_value:,.0f}원 중 "
            f"{max_inv:,.0f}원만 투자"
        )
        total_value = max_inv

    executor = OrderExecutor(initial_value=total_value)

    # ── 서킷브레이커 재진입 확인 ──
    if not executor.check_circuit_breaker_reentry(total_value):
        notifier.send(
            f"서킷브레이커 유지 중 — 현금 대피 상태 "
            f"(DD 회복 시 자동 재진입). 리밸런싱 건너뜀."
        )
        return

    screener = MultiFactorScreener()

    # 새 포트폴리오 계산
    today_str = datetime.now().strftime("%Y%m%d")
    portfolio_df = screener.screen(today_str)
    new_portfolio = portfolio_df.index.tolist() if not portfolio_df.empty else []
    logger.info(f"신규 포트폴리오: {len(new_portfolio)}개 종목")

    if not new_portfolio:
        notifier.send("스크리닝 결과가 비어 있어 리밸런싱을 건너뜁니다.")
        return

    # 팩터 스코어 & 포트폴리오 DB 저장
    _save_screening_results(today_str, portfolio_df)

    # ── 시장 레짐 필터 ──
    invest_ratio = 1.0
    try:
        from data.collector import KRXDataCollector
        from strategy.market_regime import MarketRegimeFilter

        collector = KRXDataCollector()
        regime_filter = MarketRegimeFilter(collector)
        invest_ratio = regime_filter.get_invest_ratio(today_str)
    except Exception as e:
        logger.warning(f"시장 레짐 필터 실패: {e} → 투자 비중 100%")

    # ── 변동성 타겟팅 ──
    vol_scale = _calc_vol_target_scale(api)
    invest_ratio *= vol_scale

    if invest_ratio < 1.0:
        logger.info(
            f"최종 투자 비중: {invest_ratio:.0%} "
            f"(레짐 × 변동성 스케일)"
        )

    # 리밸런싱 주문 실행
    sell_done, buy_done = executor.execute_rebalancing(
        current_holdings, new_portfolio, invest_ratio=invest_ratio
    )

    # 결과 알림
    updated_balance = api.get_balance()
    cash = updated_balance.get("cash", 0)
    eval_amt = updated_balance.get("total_eval_amount", 0)
    # 총 자산 = 평가금액 + 예수금 (체결 직후 평가 미반영 대비)
    total_asset = eval_amt + cash if eval_amt > 0 else cash or total_value
    notifier.send_rebalancing_report(
        sell_done=sell_done,
        buy_done=buy_done,
        total_value=total_asset,
        balance=updated_balance,
    )


def run_monthly_rebalancing() -> None:
    """월말 리밸런싱 실행 (스케줄러 호출)

    - 영업일이 아니거나 월말이 아니면 스킵
    - 리밸런싱 실패 시 텔레그램 에러 알림
    """
    if not (is_business_day() and is_last_business_day_of_month()):
        return

    logger.info("=" * 50)
    logger.info("월말 리밸런싱 시작")
    notifier = TelegramNotifier()
    notifier.send("월말 리밸런싱을 시작합니다...")

    try:
        _execute_rebalancing_core(notifier)
        logger.info("월말 리밸런싱 완료")
    except Exception as e:
        logger.error(f"리밸런싱 오류: {e}", exc_info=True)
        notifier.send_error(str(e))


def run_daily_defense_check() -> None:
    """장 마감 전 일별 방어 체크 (15:15 실행)

    MDD 서킷브레이커와 트레일링 스톱을 매일 체크하여
    급락 시 당일 장중 시장가 매도로 대응합니다.
    """
    if not is_business_day():
        return

    logger.info("일별 방어 체크 시작 (15:15)")
    notifier = TelegramNotifier()

    try:
        api = KiwoomRestClient()
        balance = api.get_balance()

        # 잔고 API 실패 감지
        if balance.get("total_eval_amount", 0) == 0 and balance.get("cash", 0) == 0:
            logger.warning("방어 체크: 잔고 API 비정상 응답 — 스킵")
            return

        holdings = balance.get("holdings", [])
        if not holdings:
            logger.info("방어 체크: 보유 종목 없음 — 스킵")
            return

        total_value = balance.get("total_eval_amount", 0)
        executor = OrderExecutor(initial_value=total_value)

        actions: list[str] = []

        # ① MDD 서킷브레이커 체크
        if executor._check_drawdown(total_value):
            sold = executor.execute_emergency_liquidation()
            actions.append(
                f"서킷브레이커 발동: 전량 매도 {len(sold)}종목 "
                f"({', '.join(sold)})"
            )
            notifier.send(
                f"[방어 체크] 서킷브레이커 발동!\n"
                f"전량 매도 완료: {len(sold)}종목\n"
                f"매도 종목: {', '.join(sold)}"
            )
            logger.warning(f"방어 체크 완료: {'; '.join(actions)}")
            return  # 전량 매도 후 트레일링 스톱 불필요

        # ② 트레일링 스톱 체크
        stop_tickers = executor._check_trailing_stops(balance)
        if stop_tickers:
            exchange = "KRX" if api.is_paper else "SOR"
            sold_tickers: list[str] = []

            for ticker in stop_tickers:
                holding = next(
                    (h for h in holdings if h.get("ticker") == ticker),
                    None,
                )
                if not holding or holding.get("qty", 0) <= 0:
                    continue

                result = api.sell_stock(
                    ticker=ticker,
                    qty=holding["qty"],
                    order_type="3",
                    exchange=exchange,
                )
                if result.get("return_code") == 0:
                    sold_tickers.append(ticker)
                    # DB 기록
                    from datetime import date as date_type

                    price = holding.get("current_price", 0)
                    qty = holding["qty"]
                    amount = price * qty
                    executor.storage.save_trade(
                        trade_date=date_type.today(),
                        ticker=ticker,
                        side="SELL",
                        quantity=qty,
                        price=price,
                        amount=amount,
                        commission=amount * executor.cfg.commission_rate,
                        tax=amount * executor.cfg.tax_rate,
                        is_paper=settings.is_paper_trading,
                    )

            if sold_tickers:
                actions.append(
                    f"트레일링 스톱: {len(sold_tickers)}종목 매도 "
                    f"({', '.join(sold_tickers)})"
                )
                notifier.send(
                    f"[방어 체크] 트레일링 스톱 발동!\n"
                    f"매도 완료: {len(sold_tickers)}종목\n"
                    f"매도 종목: {', '.join(sold_tickers)}"
                )

        if actions:
            logger.warning(f"방어 체크 완료: {'; '.join(actions)}")
        else:
            logger.info("방어 체크 완료: 이상 없음")

    except Exception as e:
        logger.error(f"방어 체크 오류: {e}", exc_info=True)
        notifier.send_error(f"방어 체크 오류: {e}")


def run_daily_report() -> None:
    """장 마감 후 상세 일별 수익 리포트 발송"""
    if not is_business_day():
        return

    notifier = TelegramNotifier()
    try:
        api = KiwoomRestClient()
        balance = api.get_balance()
        notifier.send_detailed_daily_report(balance)
    except Exception as e:
        logger.error(f"일별 리포트 오류: {e}")
        notifier.send_error(str(e))


# ────────────────────────────────────────────
# 스케줄러 설정 및 실행
# ────────────────────────────────────────────


def _force_rebalancing() -> None:
    """즉시 리밸런싱 실행 (월말 체크 무시, 수동 실행용)"""
    logger.info("=" * 50)
    logger.info("수동 리밸런싱 시작 (--now)")
    notifier = TelegramNotifier()
    notifier.send("[수동] 리밸런싱을 즉시 실행합니다...")

    try:
        _execute_rebalancing_core(notifier)
        logger.info("수동 리밸런싱 완료")
    except Exception as e:
        logger.error(f"리밸런싱 오류: {e}", exc_info=True)
        notifier.send_error(str(e))


def _save_screening_results(date_str: str, portfolio_df: "pd.DataFrame") -> None:
    """스크리닝 결과를 DB에 저장 (팩터 스코어 + 포트폴리오)

    Args:
        date_str: 기준 날짜 (YYYYMMDD)
        portfolio_df: screener.screen() 반환값
            (index=ticker, columns=[value_score, momentum_score,
            quality_score, composite_score, weight])
    """
    import pandas as pd

    if portfolio_df.empty:
        return

    try:
        dt = datetime.strptime(date_str, "%Y%m%d").date()
        storage = DataStorage()

        # 팩터 스코어 저장
        score_cols = [
            c for c in ["value_score", "momentum_score", "quality_score", "composite_score"]
            if c in portfolio_df.columns
        ]
        if score_cols:
            storage.save_factor_scores(dt, portfolio_df[score_cols])
            logger.info(f"팩터 스코어 DB 저장: {len(portfolio_df)}건")

        # 포트폴리오 저장
        port_df = portfolio_df.reset_index()
        port_df = port_df.rename(columns={port_df.columns[0]: "ticker"})
        # 종목명 추가
        from data.collector import KRXDataCollector

        collector = KRXDataCollector()
        port_df["name"] = port_df["ticker"].apply(
            lambda t: collector.get_ticker_name(t) or t
        )
        storage.save_portfolio(dt, port_df)
        logger.info(f"포트폴리오 DB 저장: {len(port_df)}건")

    except Exception as e:
        logger.error(f"스크리닝 결과 DB 저장 실패: {e}", exc_info=True)


def main() -> None:
    """스케줄러 메인 엔트리포인트"""
    parser = argparse.ArgumentParser(description="퀀트 자동매매 스케줄러")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="스케줄러 시작 없이 설정만 확인",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="월말 체크 무시, 즉시 리밸런싱 1회 실행 후 종료",
    )
    parser.add_argument(
        "--screen-only",
        action="store_true",
        help="스크리닝만 실행 (매매 없이 종목 목록만 확인)",
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

    if args.screen_only:
        from strategy.screener import MultiFactorScreener

        screener = MultiFactorScreener()
        today_str = datetime.now().strftime("%Y%m%d")
        logger.info(f"[스크리닝 전용] 기준일: {today_str}")
        portfolio_df = screener.screen(today_str)
        if portfolio_df.empty:
            logger.info("스크리닝 결과 없음")
        else:
            # 스크리너 내부 collector 재사용 (종목명 캐시 활용)
            collector = screener.collector
            logger.info(f"선정 종목 ({len(portfolio_df)}개):")
            for ticker in portfolio_df.index:
                name = collector.get_ticker_name(ticker) or ticker
                score = portfolio_df.loc[ticker, "composite_score"]
                logger.info(f"  {ticker} {name}: 복합스코어 {score:.1f}")
        return

    if args.now:
        _force_rebalancing()
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
        run_daily_defense_check,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=15,
        id="daily_defense_check",
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
    logger.info("  08:50 월말 리밸런싱 | 15:15 방어 체크 | 15:35 일별 리포트")
    TelegramNotifier().send("퀀트 스케줄러가 시작되었습니다.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
