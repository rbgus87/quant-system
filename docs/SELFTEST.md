# SELFTEST — 분기 리밸런싱 전야 자가 검증

`scripts/selftest.py` 는 분기 리밸런싱 D-1 에 수동 실행해
"exe 빌드 + 외부 API + DB + 팩터 파이프라인" 이 지금 이 순간
문제없이 돌 수 있는지를 5~10분 안에 확인하는 스크립트다.

quant-system 은 day-trader 와 달리 매일 돌리는 시스템이 아니라
분기 리밸런싱 시점에만 실전 호출이 발생한다. 실전 호출 직전에
"배포 무결성" (특히 exe 빌드 누락 모듈, API 키 만료) 을 점검하는 게 목적.

---

## 실행

```bash
# 기본 — 전 단계 실행, 결과는 터미널에만
python scripts/selftest.py

# 결과 요약을 텔레그램으로도 전송
python scripts/selftest.py --notify

# pytest 가 오래 걸려서 먼저 빠르게 네트워크만 보고 싶을 때
python scripts/selftest.py --skip-tests

# 빌드 전이라 PYZ TOC 가 없을 때 (빌드 파이프라인 점검 중)
python scripts/selftest.py --skip-exe-check

# 예외 stacktrace 까지 전부 보고 싶을 때
SELFTEST_DEBUG=1 python scripts/selftest.py
```

exit code: 0 = 전 단계 통과, 1 = FAIL 이 하나라도 있음.
CI 나 스케줄러 전처리에서 그대로 연결 가능.

---

## 검증 4단계

### Phase 1. 정적 분석 (수초)

| # | 검증 | 실패 시 조치 |
|---|------|-------------|
| 1.1 | `ruff check .` | 린트 위반 수정 |
| 1.2 | `ruff format --check .` (WARN) | `ruff format .` 실행 |
| 1.3 | `scripts/scan_imports.py` → `build_exe.py` 의 `--hidden-import` 대조 | 누락 모듈을 `build_exe.py` 에 추가 후 재빌드 |
| 1.4 | `.env` 필수 키 존재 (KIWOOM_*, DART_API_KEY, TELEGRAM_*, KRX_OPENAPI_KEY) | `.env` 보충 |

### Phase 2. 단위 테스트 (수십 초 ~ 수 분)

`pytest tests/ --tb=short -q` 를 돌려 실패가 있으면 FAIL.
`--skip-tests` 로 건너뛸 수 있음.

### Phase 3. 통합 스모크 (네트워크 필요)

| # | 검증 | 기준 |
|---|------|------|
| 3.1 | DART API 핑 | 삼성전자(00126380) 2023 사업보고서 재무 1건 조회 성공 |
| 3.2 | 키움 토큰 (paper) | `IS_PAPER_TRADING=true` 일 때 mockapi 토큰 발급 성공 |
| 3.3 | 텔레그램 | `--notify` 없으면 `getMe` 핑, 있으면 "self-test 시작" 메시지 전송 |
| 3.4 | SQLite 테이블 | `data/quant.db` 에 6개 핵심 테이블 존재 |
| 3.5 | 스크리너 1회 | 전일 영업일 기준 `MultiFactorScreener.screen()` 이 비어있지 않은 DF 반환 |

> **3.2 주의**: 실전 모드(`IS_PAPER_TRADING=false`)면 WARN 으로 스킵됨.
> 리밸런싱 D-1 검증은 반드시 paper 로 먼저 통과시킬 것.

### Phase 4. exe 번들 검증

`build/KoreanQuant/PYZ-00.toc` 를 파싱해
다음 모듈이 전부 번들에 포함돼 있는지 확인:

- `monitor.*` (snapshot, risk_guard, benchmark, drift, alert, storage)
- `dart_notifier.*` (notifier, filter)
- `quantstats` (리포트 엔진)
- `scheduler.main`
- `data.*`, `factors.*`, `strategy.*`, `trading.*`, `notify.*`

누락이 하나라도 있으면 FAIL → `build_exe.py` 의 `--hidden-import` 보강 후
`python build_exe.py` 재실행.

추가로 `KoreanQuant.exe` 파일 자체의 존재·크기·빌드 나이를 표시.
30일 이상 된 빌드는 WARN.

---

## 리밸런싱 D-1 체크리스트 (예: 2026-06-30 리밸런싱 → 6/29 실행)

분기 리밸런싱 신호는 매 분기 마지막 영업일, 실제 주문은 다음 영업일 시가다.
**아래는 6/29 (D-1) 에 수동으로 한 번 돌리는 템플릿**:

```
[ ] 1. git pull && 의존성 최신화 확인
      pip install -r requirements.txt

[ ] 2. 데이터 최신화 (선택)
      python -m data.collector --update

[ ] 3. 자가 검증 실행
      python scripts/selftest.py --notify

      → 전 Phase OK 이면 텔레그램에 요약 도착

[ ] 4. FAIL / WARN 대응
      [ ] Phase 1 FAIL → ruff 수정 / hidden-import 추가 / .env 보충
      [ ] Phase 2 FAIL → 단위 테스트 수정 후 재실행
      [ ] Phase 3 FAIL → API 키·네트워크 / DART·키움 상태 페이지 확인
      [ ] Phase 4 FAIL → build_exe.py 보강 후 재빌드
                         python build_exe.py

[ ] 5. exe 재빌드했다면 selftest 재실행 (--skip-tests 로 빠르게)

[ ] 6. 스케줄러 상태 확인
      [ ] IS_PAPER_TRADING 값이 의도한 대로인지 (.env 확인)
      [ ] APScheduler 가 돌고 있는지 (GUI / 로그)

[ ] 7. 예비 리밸런싱 시뮬레이션 (옵션)
      # paper 모드에서 월말 체결 시나리오 재현
      python -m strategy.rebalancer --dry-run

[ ] 8. 당일 손익·현금·보유 종목 확인 후 취침
```

---

## 자주 걸리는 이슈

**Phase 1.3 MISSING external  quantstats**
: `build_exe.py` 의 `--hidden-import` 리스트에 `quantstats` 추가. PyInstaller 는 `report.py` 에서 간접 참조하는 `quantstats` 를 자동으로 잡지 못함.

**Phase 3.1 DART status=013 (조회된 데이타가 없습니다)**
: DART API 키는 유효하나 요청 파라미터 문제. 키 자체 문제는 `status=010` (등록되지 않은 키) 또는 `020` (사용할 수 없는 키).

**Phase 3.2 FAIL 토큰 발급 실패**
: `IS_PAPER_TRADING=true` 인지 먼저 확인. 실전 키로 mockapi 호출하면 실패함.

**Phase 3.5 스크리너 결과 비어있음**
: KRX Open API 키 만료 가능성. `KRX_OPENAPI_KEY` 갱신 후 재실행. 또는 일 10,000건 한도 초과 — 다음 날 재시도.

**Phase 4 "PYZ-00.toc 없음"**
: 아직 `python build_exe.py` 실행 안 함. `--skip-exe-check` 로 1~3단계만 돌리거나 먼저 빌드.
