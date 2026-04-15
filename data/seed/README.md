# data/seed/

외부 시스템에서 다운로드하여 DB 임포트에 사용하는 시드 파일. 라이선스·크기 이슈로 git에 추적하지 않음 (`README.md` 제외).

## 파일 목록

### delisted_stocks.xls — 상장폐지 종목 목록

| 항목 | 값 |
|------|----|
| 출처 | KRX KIND 시스템 (https://kind.krx.co.kr) |
| 경로 | 상장/폐지 > **상장폐지현황** |
| 형식 | HTML 테이블 (확장자는 `.xls`), EUC-KR |
| 컬럼 | 번호, 회사명, 종목코드, 폐지일자, 폐지사유, 비고 |
| 갱신 주기 | **분기 1회** (리밸런싱 D-7 권장) |
| 임포트 | `python scripts/import_delisted.py` |

**갱신 절차**:
1. KIND 접속 → 상장폐지현황 → 전체 조회 후 Excel 다운로드
2. `data/seed/delisted_stocks.xls`로 저장 (덮어쓰기)
3. `python scripts/import_delisted.py` 실행 → DB upsert
4. 요약 출력에서 신규 폐지 건수 확인

**용도**:
- 백테스트 생존자 편향 보정 (`scripts/backtest_with_delisted.py`)
- 리스크 감시: 보유 종목 폐지 임박 감지 (`monitor/risk_guard.check_delisting_imminent`)
- F-Score 필터 효과 검증 (`scripts/verify_fscore_effectiveness.py`)
