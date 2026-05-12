# monitor/alert.py
"""리스크 경고 메시지 발송

notify/telegram.py의 TelegramNotifier.send()를 사용하여
리스크 경고를 Telegram으로 발송한다.
"""

import logging

from notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


def _format_stop_loss(alert: dict) -> str:
    """종목별 손절 경고 메시지"""
    return (
        f"\U0001f6a8 *손절 경고*\n"
        f"종목: {alert['name']} ({alert['ticker']})\n"
        f"현재가: {alert['current_price']:,.0f}원\n"
        f"매입가: {alert['avg_price']:,.0f}원\n"
        f"수익률: {alert['profit_rate']:+.1f}%\n"
        f"손절 기준: {alert['threshold']:.0f}%\n"
        f"\u26a0\ufe0f 알림 전용 — 자동 매도 아님"
    )


def _format_drawdown(alert: dict) -> str:
    """포트폴리오 드로다운 경고 메시지"""
    return (
        f"\U0001f534 *포트폴리오 드로다운 경고*\n"
        f"총평가: {alert['total_eval']:,.0f}원\n"
        f"투자원금: {alert['invested']:,.0f}원\n"
        f"손실률: {alert['loss_pct']:+.1f}%\n"
        f"경고 기준: {alert['threshold']:.0f}%\n"
        f"\u26a0\ufe0f 포트폴리오 전체 점검 권장"
    )


def _format_delisting(alert: dict) -> str:
    """관리종목 경고 메시지"""
    return (
        f"\u26a0\ufe0f *관리종목 지정 감지*\n"
        f"종목: {alert['name']} ({alert['ticker']})\n"
        f"보유수량: {alert['qty']}주\n"
        f"현재가: {alert['current_price']:,.0f}원\n"
        f"\u26a0\ufe0f 매도 검토 필요"
    )


_FORMATTERS = {
    "stop_loss": _format_stop_loss,
    "drawdown": _format_drawdown,
    "delisting": _format_delisting,
}


_ACTION_ICON = {
    "sold": "✅",       # ✅
    "dry_run": "\U0001f4dd", # 📝
    "failed": "❌",     # ❌
    "skipped": "⏭️",  # ⏭️
}


def format_delisting_auto_sell_message(actions: list[dict]) -> str:
    """폐지 임박 자동 매도 결과 알림 메시지.

    dry_run 건과 실매도 건을 구분하여 한 메시지로 묶어 표시.

    Args:
        actions: RiskGuard.execute_delisting_auto_sell() 반환값

    Returns:
        텔레그램 발송용 메시지 문자열
    """
    if not actions:
        return ""

    has_real = any(not a.get("dry_run", True) for a in actions)
    header = (
        "\U0001f534 *폐지 임박 자동 매도*"
        if has_real
        else "\U0001f4dd *폐지 임박 자동 매도 (DRY-RUN)*"
    )

    lines: list[str] = [header, ""]
    for a in actions:
        icon = _ACTION_ICON.get(a.get("action", ""), "")
        ticker = a.get("ticker", "")
        name = a.get("name", ticker)
        qty = a.get("qty", 0)
        category = a.get("category", "")
        days_until = a.get("days_until", 0)
        delist_date = a.get("delist_date", "")
        order_id = a.get("order_id", "")
        action = a.get("action", "")

        suffix = ""
        if action == "sold" and order_id:
            suffix = f"  주문번호: {order_id}"
        elif action == "dry_run":
            suffix = "  (실주문 미발생)"
        elif action == "failed":
            suffix = "  ⚠️ 매도 실패 — 수동 확인 필요"
        elif action == "skipped":
            suffix = "  (이미 처리됨)"

        lines.append(
            f"{icon} {name} ({ticker}) {qty}주\n"
            f"   카테고리: {category} | 폐지일: {delist_date} (D-{days_until}){suffix}"
        )

    if not has_real:
        lines.append("")
        lines.append("⚠️ dry_run 모드 — 실제 주문은 발생하지 않았습니다.")

    return "\n".join(lines)


def send_risk_alerts(alerts: list[dict]) -> int:
    """리스크 경고를 Telegram으로 발송한다.

    Args:
        alerts: RiskGuard.check_all() 반환값

    Returns:
        발송 성공 건수
    """
    if not alerts:
        return 0

    notifier = TelegramNotifier()
    sent = 0

    for alert in alerts:
        alert_type = alert.get("type", "")
        formatter = _FORMATTERS.get(alert_type)
        if formatter is None:
            logger.warning("알 수 없는 경고 타입: %s", alert_type)
            continue

        msg = formatter(alert)
        if notifier.send(msg):
            sent += 1
            logger.info(
                "리스크 경고 발송: %s — %s (%s)",
                alert_type,
                alert.get("name", ""),
                alert.get("ticker", ""),
            )
        else:
            logger.error(
                "리스크 경고 발송 실패: %s — %s",
                alert_type,
                alert.get("ticker", ""),
            )

    return sent
