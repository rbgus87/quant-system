# notify/test_report.py
"""텔레그램 리포트 테스트 CLI

모의 데이터로 상세 일별 리포트를 발송합니다.
실제 키움 API 없이 텔레그램 메시지 형식을 확인할 수 있습니다.

사용법:
  python notify/test_report.py              # 모의 데이터로 텔레그램 발송
  python notify/test_report.py --dry-run    # 발송 없이 메시지만 출력
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notify.telegram import TelegramNotifier


MOCK_BALANCE = {
    "holdings": [
        {
            "ticker": "005930",
            "name": "삼성전자",
            "qty": 100,
            "avg_price": 55000,
            "current_price": 56800,
            "eval_amount": 5680000,
            "eval_profit": 180000,
            "profit_rate": 3.27,
        },
        {
            "ticker": "000660",
            "name": "SK하이닉스",
            "qty": 20,
            "avg_price": 110000,
            "current_price": 108500,
            "eval_amount": 2170000,
            "eval_profit": -30000,
            "profit_rate": -1.36,
        },
        {
            "ticker": "005380",
            "name": "현대차",
            "qty": 15,
            "avg_price": 120000,
            "current_price": 129720,
            "eval_amount": 1945800,
            "eval_profit": 145800,
            "profit_rate": 8.10,
        },
        {
            "ticker": "035420",
            "name": "NAVER",
            "qty": 8,
            "avg_price": 180000,
            "current_price": 175200,
            "eval_amount": 1401600,
            "eval_profit": -38400,
            "profit_rate": -2.67,
        },
        {
            "ticker": "051910",
            "name": "LG화학",
            "qty": 5,
            "avg_price": 350000,
            "current_price": 372000,
            "eval_amount": 1860000,
            "eval_profit": 110000,
            "profit_rate": 6.29,
        },
        {
            "ticker": "006400",
            "name": "삼성SDI",
            "qty": 10,
            "avg_price": 250000,
            "current_price": 243500,
            "eval_amount": 2435000,
            "eval_profit": -65000,
            "profit_rate": -2.60,
        },
        {
            "ticker": "068270",
            "name": "셀트리온",
            "qty": 25,
            "avg_price": 170000,
            "current_price": 178500,
            "eval_amount": 4462500,
            "eval_profit": 212500,
            "profit_rate": 5.00,
        },
        {
            "ticker": "035720",
            "name": "카카오",
            "qty": 30,
            "avg_price": 42000,
            "current_price": 43200,
            "eval_amount": 1296000,
            "eval_profit": 36000,
            "profit_rate": 2.86,
        },
        {
            "ticker": "105560",
            "name": "KB금융",
            "qty": 40,
            "avg_price": 62000,
            "current_price": 65300,
            "eval_amount": 2612000,
            "eval_profit": 132000,
            "profit_rate": 5.32,
        },
        {
            "ticker": "055550",
            "name": "신한지주",
            "qty": 50,
            "avg_price": 38000,
            "current_price": 39700,
            "eval_amount": 1985000,
            "eval_profit": 85000,
            "profit_rate": 4.47,
        },
    ],
    "cash": 312500,
    "total_eval_amount": 26159900,
    "total_profit": 767900,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="텔레그램 리포트 테스트")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="발송 없이 메시지만 콘솔 출력",
    )
    args = parser.parse_args()

    notifier = TelegramNotifier()

    if args.dry_run:
        # 메시지 생성만 하고 출력
        print("=" * 50)
        print("  텔레그램 메시지 미리보기 (dry-run)")
        print("=" * 50)

        # send를 가로채서 출력만
        original_send = notifier.send
        def mock_send(message: str, parse_mode: str = "Markdown") -> bool:
            print(message)
            return True
        notifier.send = mock_send  # type: ignore[assignment]

        notifier.send_detailed_daily_report(MOCK_BALANCE)
        print("=" * 50)
    else:
        print("모의 데이터로 텔레그램 상세 리포트를 발송합니다...")
        ok = notifier.send_detailed_daily_report(MOCK_BALANCE)
        if ok:
            print("발송 성공!")
        else:
            print("발송 실패. .env 파일의 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 확인하세요.")


if __name__ == "__main__":
    main()
