# scheduler/main.py
"""자동매매 스케줄러 (APScheduler 3.x 기반)

실행:
  python scheduler/main.py
  python scheduler/main.py --dry-run

스케줄:
  - 매 영업일 {rebalance_time}  → 월말이면 리밸런싱 신호 계산 실행 (config.yaml)
  - 매 영업일 15:15  → 일별 방어 체크 (MDD 서킷브레이커 + 트레일링 스톱)
  - 매 영업일 15:35  → 일별 수익 리포트 발송
"""

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from config.logging_config import setup_logging
from config.calendar import is_krx_business_day, is_last_krx_business_day_of_month
from data.storage import DataStorage
from trading.kiwoom_api import KiwoomRestClient
from trading.order import OrderExecutor
from notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# 모듈 레벨 스케줄러 참조 (재시도 예약용)
_scheduler_instance = None


def _install_crash_handler() -> None:
    """미처리 예외를 파일 + 텔레그램으로 기록하는 excepthook 설치"""

    def _excepthook(
        exc_type: type,
        exc_value: BaseException,
        exc_traceback: Optional[object],
    ) -> None:
        tb_str = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )

        # 크래시 덤프 파일
        crash_dir = Path("logs")
        crash_dir.mkdir(exist_ok=True)
        crash_file = crash_dir / f"crash_{datetime.now():%Y%m%d_%H%M%S}.log"
        try:
            crash_file.write_text(tb_str, encoding="utf-8")
        except Exception:
            pass

        logger.critical(f"미처리 예외 발생:\n{tb_str}")

        # 텔레그램 즉시 알림
        try:
            TelegramNotifier().send(
                f"🚨 스케줄러 크래시\n"
                f"{exc_type.__name__}: {exc_value}\n"
                f"시각: {datetime.now():%Y-%m-%d %H:%M:%S}"
            )
        except Exception:
            pass

        sys.exit(1)

    sys.excepthook = _excepthook


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

    market_regime.calc_vol_target_scale() 공통 함수에 위임.

    Args:
        api: KiwoomRestClient 인스턴스

    Returns:
        투자 비중 배율 (0.2 ~ 1.0)
    """
    from strategy.market_regime import calc_vol_target_scale

    vol_target = settings.trading.vol_target
    lookback = settings.trading.vol_lookback_days

    if vol_target is None or vol_target <= 0:
        return 1.0

    try:
        from data.collector import KRXDataCollector

        collector = KRXDataCollector()
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=int(lookback * 1.5))
        start_str = start_dt.strftime("%Y%m%d")
        end_str = end_dt.strftime("%Y%m%d")

        df = collector.get_ohlcv("069500", start_str, end_str)  # KODEX 200
        if df is None or df.empty or len(df) < max(lookback, 20):
            logger.info("변동성 타겟팅: 데이터 부족 -> 비중 1.0")
            return 1.0

        values = df["close"].tolist()
        return calc_vol_target_scale(values, vol_target, lookback)

    except Exception as e:
        logger.warning(f"변동성 타겟팅 실패: {e} -> 비중 1.0")
        return 1.0


def _filter_already_traded_today(
    current_holdings: list[str],
    target_portfolio: list[str],
    storage: DataStorage,
) -> tuple[list[str], list[str], bool]:
    """당일 이미 체결된 주문을 current/target 리스트에서 제외 (재시도 중복 방지)

    - 오늘 매도 체결(quantity>0) 된 종목 → current_holdings에서 제외 (이미 팔았음)
    - 오늘 매수 체결(quantity>0, amount>0) 된 종목 → target_portfolio에서 제외
    - qty=0 또는 amount=0 매수 기록은 "체결 실패 마커"이므로 필터링 대상 아님

    Args:
        current_holdings: 현재 보유 종목
        target_portfolio: 목표 포트폴리오
        storage: DataStorage 인스턴스

    Returns:
        (filtered_current, filtered_target, is_retry)
    """
    from datetime import date as _date

    today = _date.today()
    try:
        trades_today = storage.load_trades(start_date=today, end_date=today)
    except Exception as e:
        logger.warning(f"당일 거래 이력 조회 실패: {e} — 필터링 건너뜀")
        return current_holdings, target_portfolio, False

    if trades_today.empty:
        return current_holdings, target_portfolio, False

    sold_mask = (trades_today["side"] == "SELL") & (trades_today["quantity"] > 0)
    sold_today = set(trades_today.loc[sold_mask, "ticker"].tolist())

    bought_mask = (
        (trades_today["side"] == "BUY")
        & (trades_today["quantity"] > 0)
        & (trades_today["amount"] > 0)
    )
    bought_today = set(trades_today.loc[bought_mask, "ticker"].tolist())

    filtered_current = [t for t in current_holdings if t not in sold_today]
    filtered_target = [t for t in target_portfolio if t not in bought_today]

    is_retry = bool(sold_today or bought_today)
    if is_retry:
        logger.warning(
            f"[재시도] 당일 처리된 주문 건너뜀: "
            f"매도 {len(sold_today)}종목, 매수 {len(bought_today)}종목"
        )
    return filtered_current, filtered_target, is_retry


def _execute_rebalancing_core(
    notifier: TelegramNotifier,
    skip_turnover_check: bool = False,
) -> None:
    """리밸런싱 공통 로직

    서킷브레이커 재진입 확인 → 스크리닝 → DB 저장 → 잔고 조회 →
    시장 레짐/변동성 타겟팅 반영 → 주문 실행 → 결과 알림

    Args:
        notifier: 텔레그램 알림 발송기
        skip_turnover_check: 턴오버 제한 검증 건너뛰기 (수동 리밸런싱 시)
    """
    import time as _time

    from strategy.screener import MultiFactorScreener

    start_ts = _time.monotonic()

    # DB 백업 (리밸런싱 전 안전장치)
    storage = DataStorage()
    try:
        storage.backup()
    except Exception as e:
        logger.warning(f"DB 백업 실패 (계속 진행): {e}")

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
    total_value = balance.get("total_eval_amount", 0) + balance.get("cash", 0)

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

    # ── 재시도 중복 방지: 당일 이미 체결된 주문은 제외 ──
    current_holdings, new_portfolio, is_retry = _filter_already_traded_today(
        current_holdings, new_portfolio, storage
    )
    if is_retry:
        logger.info(
            f"[재시도] 필터링 후 보유 {len(current_holdings)}종목, "
            f"목표 {len(new_portfolio)}종목"
        )

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
    raw_ratio = invest_ratio * vol_scale
    # 곱셈 효과로 과도한 비중 축소 방지: 최소 20% 투자 보장
    invest_ratio = max(raw_ratio, 0.20)

    if invest_ratio < 1.0:
        logger.info(
            f"최종 투자 비중: {invest_ratio:.0%} "
            f"(레짐 × 변동성 스케일, raw={raw_ratio:.0%}, 하한 20%)"
        )

    # 리밸런싱 주문 실행
    sell_done, buy_done = executor.execute_rebalancing(
        current_holdings, new_portfolio,
        invest_ratio=invest_ratio,
        skip_turnover_check=skip_turnover_check,
    )

    # 결과 알림
    elapsed = _time.monotonic() - start_ts
    updated_balance = api.get_balance()
    cash = updated_balance.get("cash", 0)
    eval_amt = updated_balance.get("total_eval_amount", 0)
    # 총 자산 = 평가금액 + 예수금 (체결 직후 평가 미반영 대비)
    total_asset = eval_amt + cash if eval_amt > 0 else cash or total_value

    # 포트폴리오 변동 요약
    prev_set = set(current_holdings)
    new_set = set(new_portfolio)
    kept = prev_set & new_set
    added = new_set - prev_set
    removed = prev_set - new_set
    change_summary = (
        f"유지 {len(kept)} / 신규 {len(added)} / 교체 {len(removed)}"
    )

    notifier.send_rebalancing_report(
        sell_done=sell_done,
        buy_done=buy_done,
        total_value=total_asset,
        balance=updated_balance,
        elapsed_sec=elapsed,
        change_summary=change_summary,
    )


def run_scheduled_rebalancing() -> None:
    """월말/분기 리밸런싱 실행 (스케줄러 호출)

    - 영업일이 아니거나 월말이 아니면 스킵
    - quarterly 모드일 때 3/6/9/12월만 실행 (백테스트 엔진과 동일)
    - 리밸런싱 실패 시 최대 2회 재시도 (30분, 60분 후)
    - 최종 실패 시 텔레그램 에러 알림
    """
    if not (is_business_day() and is_last_business_day_of_month()):
        return

    # 분기 리밸런싱: 3/6/9/12월만 실행
    freq = settings.portfolio.rebalance_frequency
    if freq == "quarterly":
        current_month = datetime.now().month
        if current_month not in (3, 6, 9, 12):
            logger.info(
                f"분기 리밸런싱 모드: {current_month}월은 리밸런싱 대상이 아닙니다 "
                f"(3/6/9/12월만 실행)"
            )
            return

    logger.info("=" * 50)
    freq_label = "분기" if freq == "quarterly" else "월말"
    logger.info(f"{freq_label} 리밸런싱 시작")
    notifier = TelegramNotifier()
    notifier.send(f"[{freq_label}] 리밸런싱을 시작합니다...")

    import time as _time

    max_retries = 2
    retry_delays_sec = [1800, 3600]  # 30분, 60분

    for attempt in range(1 + max_retries):
        try:
            _execute_rebalancing_core(notifier)
            logger.info(f"{freq_label} 리밸런싱 완료")
            return
        except Exception as e:
            if attempt < max_retries:
                delay = retry_delays_sec[attempt]
                logger.warning(
                    f"리밸런싱 실패 (시도 {attempt + 1}/{1 + max_retries}): {e} "
                    f"— {delay // 60}분 후 재시도"
                )
                notifier.send(
                    f"리밸런싱 실패 (시도 {attempt + 1}): {e}\n"
                    f"{delay // 60}분 후 자동 재시도합니다."
                )
                _time.sleep(delay)
            else:
                logger.error(
                    f"리밸런싱 최종 실패 ({1 + max_retries}회 시도 모두 실패): {e}",
                    exc_info=True,
                )
                notifier.send_error(
                    f"리밸런싱 최종 실패 ({1 + max_retries}회 시도 모두 실패): {e}"
                )


def run_daily_defense_check() -> None:
    """장 마감 전 일별 방어 체크 (15:15 실행)

    MDD 서킷브레이커와 트레일링 스톱을 매일 체크하여
    급락 시 당일 장중 시장가 매도로 대응합니다.
    """
    if not is_business_day():
        return

    logger.info("일별 방어 체크 시작 (15:15)")
    notifier = TelegramNotifier()

    def _ticker_display(ticker: str) -> str:
        """종목코드 → '종목명(코드)' 표시"""
        h = next((h for h in holdings if h.get("ticker") == ticker), None)
        name = h.get("name", "") if h else ""
        return f"{name}({ticker})" if name else ticker

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
            sold_names = [_ticker_display(t) for t in sold]
            actions.append(
                f"서킷브레이커 발동: 전량 매도 {len(sold)}종목 "
                f"({', '.join(sold_names)})"
            )
            notifier.send(
                f"[방어 체크] 서킷브레이커 발동!\n"
                f"전량 매도 완료: {len(sold)}종목\n"
                f"매도 종목: {', '.join(sold_names)}"
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
                sold_names = [_ticker_display(t) for t in sold_tickers]
                actions.append(
                    f"트레일링 스톱: {len(sold_tickers)}종목 매도 "
                    f"({', '.join(sold_names)})"
                )
                notifier.send(
                    f"[방어 체크] 트레일링 스톱 발동!\n"
                    f"매도 완료: {len(sold_tickers)}종목\n"
                    f"매도 종목: {', '.join(sold_names)}"
                )

        if actions:
            logger.warning(f"방어 체크 완료: {'; '.join(actions)}")
        else:
            logger.info("방어 체크 완료: 이상 없음")

    except Exception as e:
        logger.error(f"방어 체크 오류: {e}", exc_info=True)
        notifier.send_error(f"방어 체크 오류: {e}")


def run_daily_report() -> None:
    """장 마감 후 상세 일별 수익 리포트 발송 + 스냅샷 DB 저장"""
    if not is_business_day():
        return

    notifier = TelegramNotifier()
    try:
        api = KiwoomRestClient()
        balance = api.get_balance()

        # 스냅샷 수집 + DB 저장
        snapshot = None
        try:
            from monitor.snapshot import take_daily_snapshot
            from monitor.storage import MonitorStorage

            snapshot = take_daily_snapshot(balance)
            MonitorStorage().save_snapshot(snapshot)
            logger.info("일간 스냅샷 저장 완료")

            # 드리프트 계산
            from monitor.drift import calculate_drift

            drift = calculate_drift(snapshot)
            if drift:
                snapshot["drift"] = drift
        except Exception as e:
            logger.error(f"스냅샷 저장 실패 (리포트는 계속 발송): {e}")

        # 기존 리포트 + 벤치마크 + 드리프트 확장
        notifier.send_detailed_daily_report(balance, snapshot=snapshot)
    except Exception as e:
        logger.error(f"일별 리포트 오류: {e}")
        notifier.send_error(str(e))


# ────────────────────────────────────────────
# 리스크 감시 (알림 전용)
# ────────────────────────────────────────────

# 모듈 레벨 싱글톤 — Job 간 _today_alerts / _delisting_cache 공유
_risk_guard = None


def _get_risk_guard():
    """RiskGuard 싱글톤을 반환한다 (lazy init)."""
    global _risk_guard
    if _risk_guard is None:
        from monitor.risk_guard import RiskGuard

        _risk_guard = RiskGuard()
    return _risk_guard


def run_risk_guard_check() -> None:
    """장중 리스크 감시 (손절 + 드로다운 + 관리종목 경고)"""
    if not is_business_day():
        return

    try:
        api = KiwoomRestClient()
        balance = api.get_balance()

        guard = _get_risk_guard()
        alerts = guard.check_all(balance)

        if alerts:
            from monitor.alert import send_risk_alerts

            sent = send_risk_alerts(alerts)
            logger.info("리스크 경고 %d건 발송 (총 %d건 감지)", sent, len(alerts))
        else:
            logger.debug("리스크 감시: 이상 없음")
    except Exception as e:
        logger.error(f"리스크 감시 오류: {e}")


def run_risk_guard_delisting() -> None:
    """관리종목 캐시 갱신 (하루 1회, 09:30)"""
    if not is_business_day():
        return

    try:
        guard = _get_risk_guard()
        guard.refresh_delisting_cache()
    except Exception as e:
        logger.error(f"관리종목 캐시 갱신 오류: {e}")


def run_auto_backfill() -> None:
    """매일 장 시작 전(09:00) 자동 백필 — 최근 5영업일 수집 누락 복구.

    전일 `run_daily_data_collection`이 KRX 업데이트 지연으로 실패한 경우를
    포착하여 아침에 자동 복구한다. 성공·실패 모두 텔레그램 알림.
    """
    if not is_business_day():
        return

    try:
        from scripts.auto_backfill_missing import detect_and_backfill, notify_result

        markets = list(settings.schedule.daily_data_collection.markets)
        missing, recovered, still_missing = detect_and_backfill(
            lookback=5, markets=markets
        )
        notify_result(missing, recovered, still_missing)
        logger.info(
            f"자동 백필 완료 — 누락 {len(missing)}일 / "
            f"복구 {len(recovered)}일 / 실패 {len(still_missing)}일"
        )
    except Exception as e:
        logger.error(f"자동 백필 오류: {e}", exc_info=True)
        try:
            TelegramNotifier().send_error(f"자동 백필 오류: {e}")
        except Exception:
            pass


def refresh_delisted_data() -> None:
    """상장폐지 데이터 월간 갱신 (매월 설정된 날짜/시각 실행).

    동작:
    1. config의 auto_download=True면 KIND에서 최신 .xls 다운로드 시도
       (2026-04 현재 KIND는 CAPTCHA 등 자동화 제약 있음 → 실패 시 수동 안내)
    2. 파일이 존재하면 `import_delisted.py`와 동일한 로직으로 DB upsert
    3. 신규 추가·업데이트 건수를 텔레그램으로 알림

    auto_download=False (기본): 텔레그램으로 수동 갱신 안내만 발송
    """
    if not is_business_day():
        return

    cfg = settings.schedule.delisted_refresh
    if not cfg.enabled:
        return

    notifier = TelegramNotifier()
    seed_path = Path("data/seed/delisted_stocks.xls")

    if not cfg.auto_download:
        # 수동 갱신 안내
        msg = (
            "📅 월간 상장폐지 데이터 갱신 필요\n\n"
            "1. KIND 접속: https://kind.krx.co.kr\n"
            "2. 상장/폐지 > 상장폐지현황 → Excel 다운로드\n"
            "3. 파일을 `data/seed/delisted_stocks.xls`로 덮어쓰기\n"
            "4. `python scripts/import_delisted.py` 실행\n\n"
            "자세한 절차: `data/seed/README.md`"
        )
        try:
            notifier.send(msg)
            logger.info("상장폐지 데이터 수동 갱신 안내 발송")
        except Exception as e:
            logger.error(f"텔레그램 발송 실패: {e}")
        return

    # auto_download=True — KIND 다운로드 시도 (현재 미구현 — KIND URL/인증 이슈)
    logger.warning(
        "auto_download=True지만 KIND 자동 다운로드는 미구현. 수동 갱신 안내로 폴백."
    )
    try:
        notifier.send(
            "⚠️ auto_download 활성화됨 — KIND 자동 다운로드는 아직 미구현입니다. "
            "data/seed/delisted_stocks.xls를 수동 갱신 후 import_delisted.py를 "
            "실행하세요."
        )
    except Exception as e:
        logger.error(f"텔레그램 발송 실패: {e}")

    # 파일이 이미 존재하면 import만 실행
    if seed_path.exists():
        try:
            from scripts.import_delisted import parse_file  # type: ignore

            rows, skipped = parse_file(seed_path)
            inserted, updated = storage_for_delisted().upsert_delisted_stocks(rows)
            summary_msg = (
                f"📊 상장폐지 DB 갱신 결과\n"
                f"- 신규 추가: {inserted}건\n"
                f"- 업데이트: {updated}건\n"
                f"- 스킵: {skipped}건"
            )
            notifier.send(summary_msg)
            logger.info(f"상장폐지 import: +{inserted} / 업데이트 {updated}")
        except Exception as e:
            logger.error(f"상장폐지 import 실패: {e}", exc_info=True)
            try:
                notifier.send_error(f"상장폐지 데이터 import 실패: {e}")
            except Exception:
                pass


def storage_for_delisted():
    """circular import 회피용 lazy 생성기."""
    return DataStorage()


def run_delisting_imminent_check(days_ahead: int = 30) -> None:
    """보유 종목 상장폐지 임박 감지 (하루 1회, 16:30).

    `delisted_stock` 테이블을 참조하여 보유 종목 중 향후 N일 내 폐지 예정
    종목을 찾아 텔레그램 긴급 알림을 발송한다. (자동 매도 없음 — 운용자 판단)
    """
    if not is_business_day():
        return

    try:
        api = KiwoomRestClient()
        balance = api.get_balance()
        if not balance.get("holdings"):
            return

        guard = _get_risk_guard()
        imminent = guard.check_delisting_imminent(balance, days_ahead=days_ahead)
        if not imminent:
            logger.debug("폐지 임박 보유 종목 없음")
            return

        notifier = TelegramNotifier()
        lines = [f"🚨 상장폐지 임박 감지 ({len(imminent)}종목) — 수동 매도 권고"]
        for a in imminent:
            lines.append(
                f"• {a['ticker']} {a['name']} — "
                f"{a['delist_date']} (D-{a['days_until']}일) "
                f"[{a['category']}] {a['reason'][:40]}"
            )
        notifier.send_error("\n".join(lines))
        logger.warning(f"폐지 임박 {len(imminent)}종목 — 텔레그램 발송")
    except Exception as e:
        logger.error(f"폐지 임박 감지 오류: {e}", exc_info=True)


# ────────────────────────────────────────────
# 일별 데이터 수집
# ────────────────────────────────────────────


def _collect_daily_data_once(date_str: str, markets: list[str]) -> tuple[int, int]:
    """해당 날짜에 대해 prefetch + fundamentals 1회 수집.

    Args:
        date_str: 기준 날짜 (YYYYMMDD)
        markets: 수집할 시장 리스트 (예: ["KOSPI"])

    Returns:
        (prefetched_rows, fundamentals_rows) 수집 결과 합계
    """
    from data.collector import KRXDataCollector

    collector = KRXDataCollector()
    prefetch_total = 0
    fund_total = 0

    for market in markets:
        # 1) OHLCV + 시가총액 (KRX Open API 1회 호출)
        pf_df = collector.prefetch_daily_trade(date_str, market=market)
        pf_count = len(pf_df) if pf_df is not None else 0
        prefetch_total += pf_count
        logger.info(
            "[%s/%s] prefetch 완료: %d 종목 (daily_price + market_cap)",
            date_str, market, pf_count,
        )

        # 2) 기본 지표 (PER/PBR/EPS/DIV/PCR — KRX API → DART 폴백)
        fund_df = collector.get_fundamentals_all(date_str, market=market)
        fund_count = len(fund_df) if fund_df is not None else 0
        fund_total += fund_count
        logger.info(
            "[%s/%s] fundamentals 완료: %d 종목",
            date_str, market, fund_count,
        )

    return prefetch_total, fund_total


def run_daily_data_collection(
    _retry_attempt: int = 0,
    _target_date_str: Optional[str] = None,
) -> None:
    """매 영업일 데이터 수집 (기본: 전일 데이터를 09:00에 수집).

    전략:
      - target="previous_business_day": 전일 확정 데이터 (09:00 권장)
      - target="today": 당일 데이터 (16:30+ 권장, KRX 지연 위험)
    재시도:
      - 실패 시 APScheduler date trigger로 재시도 예약 (time.sleep 미사용)
      - 최대 2회 재시도 (10분, 60분 후)
      - 최종 실패 시 텔레그램 에러 알림
    """
    if not is_business_day():
        logger.info("일별 데이터 수집: 휴장일이므로 스킵")
        return

    cfg = settings.schedule.daily_data_collection
    if not cfg.enabled:
        logger.info("일별 데이터 수집: config에서 비활성화됨")
        return

    # ── 수집 대상 날짜 결정 ──
    if _target_date_str:
        target_str = _target_date_str
    elif cfg.target == "previous_business_day":
        from config.calendar import previous_krx_business_day
        target_date = previous_krx_business_day(datetime.now().date())
        target_str = target_date.strftime("%Y%m%d")
    else:
        target_str = datetime.now().strftime("%Y%m%d")

    markets = cfg.markets

    # ── 멱등성 체크: 이미 충분한 데이터가 있으면 스킵 ──
    try:
        storage = DataStorage()
        from datetime import date as _date
        dt = datetime.strptime(target_str, "%Y%m%d").date()
        existing = storage.load_daily_prices_for_date(dt, market=markets[0])
        if existing >= 900:
            logger.info(
                "[%s] 이미 수집됨 (%d종목), 스킵", target_str, existing
            )
            return
    except Exception:
        pass  # 체크 실패 시 수집 진행

    logger.info(
        "일별 데이터 수집 시작 — 기준일 %s, 시장 %s (시도 %d/3)",
        target_str, "+".join(markets), _retry_attempt + 1,
    )

    # ── 수집 시도 ──
    retry_delays_sec = [600, 3600]  # 10분, 60분
    max_attempts = 1 + len(retry_delays_sec)

    try:
        pf_total, fund_total = _collect_daily_data_once(target_str, markets)
        if pf_total == 0 and fund_total == 0:
            raise RuntimeError(
                f"[{target_str}] 수집 결과가 비어 있음 "
                f"(prefetch=0, fundamentals=0)"
            )
        logger.info(
            "일별 데이터 수집 완료 — prefetch %d건, fundamentals %d건",
            pf_total, fund_total,
        )
    except Exception as e:
        if _retry_attempt < len(retry_delays_sec):
            delay = retry_delays_sec[_retry_attempt]
            logger.warning(
                "일별 데이터 수집 실패 (시도 %d/%d): %s — %d분 후 재시도",
                _retry_attempt + 1, max_attempts, e, delay // 60,
            )
            # APScheduler date trigger로 재시도 예약 (스레드 즉시 반환)
            _schedule_collection_retry(
                _retry_attempt + 1, target_str, delay
            )
        else:
            logger.error(
                "일별 데이터 수집 최종 실패 (%d회 시도 모두 실패): %s",
                max_attempts, e, exc_info=True,
            )
            TelegramNotifier().send_error(
                f"일별 데이터 수집 최종 실패 ({max_attempts}회 재시도)\n"
                f"기준일: {target_str}\n"
                f"오류: {e}"
            )


def _schedule_collection_retry(
    attempt: int, target_date_str: str, delay_sec: int
) -> None:
    """APScheduler date trigger로 재시도 예약 (time.sleep 대신)"""
    from apscheduler.schedulers import SchedulerNotRunningError

    try:
        run_at = datetime.now() + timedelta(seconds=delay_sec)
        # APScheduler 인스턴스 접근 — 모듈 레벨 변수 사용
        if _scheduler_instance is not None:
            _scheduler_instance.add_job(
                run_daily_data_collection,
                trigger="date",
                run_date=run_at,
                kwargs={
                    "_retry_attempt": attempt,
                    "_target_date_str": target_date_str,
                },
                id=f"daily_collection_retry_{attempt}",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info(
                "데이터 수집 재시도 예약: %s (시도 %d)",
                run_at.strftime("%H:%M:%S"), attempt + 1,
            )
        else:
            logger.error("스케줄러 인스턴스 없음 — 재시도 예약 불가")
    except SchedulerNotRunningError:
        logger.error("스케줄러가 실행 중이 아님 — 재시도 예약 불가")
    except Exception as e:
        logger.error(f"재시도 예약 실패: {e}")


# ────────────────────────────────────────────
# DART 공시 알림
# ────────────────────────────────────────────


def run_dart_disclosure_poll() -> None:
    """DART 공시 폴링 (장중, config 기반)"""
    if not is_business_day():
        return

    dn = settings.dart_notifier
    if not dn.enabled:
        return

    # 장중 시간 체크
    if dn.market_hours_only:
        now = datetime.now()
        try:
            t_open = datetime.strptime(dn.market_open, "%H:%M").time()
            t_close = datetime.strptime(dn.market_close, "%H:%M").time()
            if not (t_open <= now.time() <= t_close):
                return
        except ValueError:
            pass  # 검증은 settings 로드 시 수행됨

    try:
        from dart_notifier.notifier import DartDisclosureNotifier

        notifier = DartDisclosureNotifier()
        notifier.poll()
    except Exception as e:
        logger.error("DART 공시 폴링 오류: %s", e)


def run_dart_daily_summary() -> None:
    """DART 일일 공시 요약"""
    if not is_business_day():
        return

    dn = settings.dart_notifier
    if not dn.enabled or not dn.daily_summary.enabled:
        return

    try:
        from dart_notifier.notifier import DartDisclosureNotifier

        notifier = DartDisclosureNotifier()
        notifier.send_daily_summary()
    except Exception as e:
        logger.error("DART 일일 공시 요약 오류: %s", e)


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
        _execute_rebalancing_core(notifier, skip_turnover_check=True)
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
    parser.add_argument(
        "--collect-now",
        action="store_true",
        help="일별 데이터 수집 Job을 즉시 1회 실행 후 종료",
    )
    args = parser.parse_args()

    setup_logging()
    _install_crash_handler()

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

    if args.collect_now:
        run_daily_data_collection()
        return

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    global _scheduler_instance
    _scheduler_instance = scheduler

    reb_h, reb_m = settings.portfolio.rebalance_time.split(":")
    scheduler.add_job(
        run_scheduled_rebalancing,
        trigger="cron",
        day_of_week="mon-fri",
        hour=int(reb_h),
        minute=int(reb_m),
        id="scheduled_rebalancing",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_daily_defense_check,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=15,
        id="daily_defense_check",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        run_daily_report,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=35,
        id="daily_report",
        misfire_grace_time=7200,  # 2h
    )

    # 리스크 감시: 장중 30분 간격 (09:00~15:00)
    scheduler.add_job(
        run_risk_guard_check,
        trigger="cron",
        day_of_week="mon-fri",
        hour="9-15",
        minute="0,30",
        id="risk_guard_check",
        misfire_grace_time=300,
    )

    # 자동 백필: 매주 월요일 09:30 — 주간 안전망 (일일 복구는 시작 시 수행)
    scheduler.add_job(
        run_auto_backfill,
        trigger="cron",
        day_of_week="mon",
        hour=9,
        minute=30,
        id="auto_backfill",
        misfire_grace_time=43200,  # 12h — 스케줄러 늦게 시작해도 실행
        coalesce=True,
    )

    # 관리종목 캐시 갱신: 하루 1회 (09:30)
    scheduler.add_job(
        run_risk_guard_delisting,
        trigger="cron",
        day_of_week="mon-fri",
        hour=9,
        minute=30,
        id="risk_guard_delisting",
        misfire_grace_time=3600,  # 1h
    )

    # 상장폐지 임박 감지: 하루 1회 (16:30, 일별 데이터 수집 30분 후)
    scheduler.add_job(
        run_delisting_imminent_check,
        trigger="cron",
        day_of_week="mon-fri",
        hour=16,
        minute=30,
        id="delisting_imminent_check",
        misfire_grace_time=600,
    )

    # 상장폐지 데이터 월간 갱신 (기본: 매월 마지막 영업일 16:00)
    dr = settings.schedule.delisted_refresh
    if dr.enabled:
        if dr.day_of_month == -1:
            # 마지막 영업일: 매일 cron 실행 + 함수 내부에서 is_last_business_day 체크
            # 단순화: 28~31일 매일 cron + is_last_business_day_of_month 가드
            def _refresh_if_last_bday() -> None:
                if is_last_business_day_of_month():
                    refresh_delisted_data()
            scheduler.add_job(
                _refresh_if_last_bday,
                trigger="cron",
                day_of_week="mon-fri",
                day="28-31",
                hour=dr.hour,
                minute=dr.minute,
                id="delisted_refresh",
                misfire_grace_time=600,
            )
        else:
            scheduler.add_job(
                refresh_delisted_data,
                trigger="cron",
                day_of_week="mon-fri",
                day=dr.day_of_month,
                hour=dr.hour,
                minute=dr.minute,
                id="delisted_refresh",
                misfire_grace_time=600,
            )

    # 일별 데이터 수집 (기본 09:00, 전일 확정 데이터)
    dc = settings.schedule.daily_data_collection
    if dc.enabled:
        scheduler.add_job(
            run_daily_data_collection,
            trigger="cron",
            day_of_week="mon-fri",
            hour=dc.hour,
            minute=dc.minute,
            id="daily_data_collection",
            misfire_grace_time=10800,  # 3h
            coalesce=True,
        )

    # DART 공시 폴링 + 일일 요약 (config 기반)
    dn = settings.dart_notifier
    if dn.enabled:
        poll_min = dn.polling_interval_minutes
        # 폴링: 장중 시간 범위에서 interval 간격
        open_h = int(dn.market_open.split(":")[0])
        close_h = int(dn.market_close.split(":")[0])
        scheduler.add_job(
            run_dart_disclosure_poll,
            trigger="cron",
            day_of_week="mon-fri",
            hour=f"{open_h}-{close_h}",
            minute=f"*/{poll_min}",
            id="dart_disclosure_poll",
            misfire_grace_time=300,
        )

        # 일일 요약
        if dn.daily_summary.enabled:
            sum_h, sum_m = dn.daily_summary.send_time.split(":")
            scheduler.add_job(
                run_dart_daily_summary,
                trigger="cron",
                day_of_week="mon-fri",
                hour=int(sum_h),
                minute=int(sum_m),
                id="dart_daily_summary",
                misfire_grace_time=300,
            )

    freq = settings.portfolio.rebalance_frequency
    freq_desc = "분기(3/6/9/12월)" if freq == "quarterly" else "월말"
    logger.info("스케줄러 시작 (Ctrl+C로 종료)")
    logger.info(
        f"  {settings.portfolio.rebalance_time} {freq_desc} 리밸런싱 | "
        f"09:00-15:00 리스크 감시 | 15:15 방어 체크 | 15:35 일별 리포트"
    )
    if dc.enabled:
        logger.info(
            "  %02d:%02d 일별 데이터 수집 (%s)",
            dc.hour, dc.minute, "+".join(dc.markets),
        )
    if dn.enabled:
        logger.info(
            "  %s-%s DART 공시 폴링 (%d분) | %s 일일 공시 요약",
            dn.market_open, dn.market_close, dn.polling_interval_minutes,
            dn.daily_summary.send_time,
        )
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    TelegramNotifier().send(f"퀀트 스케줄러가 시작되었습니다.\n{now_str}")

    # ── 시작 시 누락 데이터 즉시 복구 ──
    _startup_recovery()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            TelegramNotifier().send(f"퀀트 스케줄러가 종료되었습니다.\n{now_str}")
        except Exception:
            pass


def _startup_recovery() -> None:
    """스케줄러 시작 시 누락 데이터 즉시 복구

    스케줄러가 늦게 시작해서 auto_backfill을 놓친 경우에도
    누락 데이터를 즉시 복구한다.
    """
    logger.info("시작 시 누락 데이터 체크")
    try:
        from scripts.auto_backfill_missing import detect_and_backfill

        markets = list(settings.schedule.daily_data_collection.markets)
        missing, recovered, still_missing = detect_and_backfill(
            lookback=5, markets=markets,
        )
        if not missing:
            logger.info("시작 시 복구: 누락 없음")
            return

        notifier = TelegramNotifier()
        if recovered:
            notifier.send(
                f"📢 시작 시 자동 복구 완료\n"
                f"누락 {len(missing)}일 / 복구 {len(recovered)}일 / "
                f"실패 {len(still_missing)}일"
            )
        if still_missing:
            notifier.send_error(
                f"⚠️ 시작 시 복구 실패 {len(still_missing)}일\n"
                f"날짜: {', '.join(str(d) for d in still_missing)}"
            )
    except Exception as e:
        logger.error(f"시작 시 복구 실패: {e}", exc_info=True)


if __name__ == "__main__":
    main()
