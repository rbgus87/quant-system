📅 6/23 (월, D-7): python -m scripts.selftest
   → 4단계 모두 PASS 확인

📅 6/27 (금, D-3): python -m scripts.backfill_data
   → 6월 말 데이터 최신화

📅 6/29 (일, D-1): python -m scheduler.main --screen-only
   → 6/30 예상 종목 미리보기 (매수는 안 함)

📅 6/30 (월, D-Day): 08:50 자동 실행
   → 09:00 체결, 텔레그램 확인


# 스케줄러가 죽어도 자동 재시작 안 되면 (watchdog 3회 한도 초과)
python -m scheduler.main

# 특정 날짜 데이터 누락 발견
python -m scripts.backfill_data --start YYYYMMDD --end YYYYMMDD

# 전체 자동 복구
python -m scripts.auto_backfill_missing

# 긴급 전량 매도 (서킷브레이커 수동 발동)
# → GUI의 "긴급 패널" 탭에서 버튼으로