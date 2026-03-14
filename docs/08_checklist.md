# 08. Phase별 개발 체크리스트

## Phase 1 — 환경 구축 (1~2일) ✅ 완료

```
■ Python 3.14 설치 및 버전 확인 (python --version)
■ VSCode + 필수 확장 설치 (Python, Pylance, Jupyter, GitLens)
■ Node.js 18+ 설치
■ Claude Code CLI 설치 (npm install -g @anthropic-ai/claude-code)
■ Claude Code CLI 인증 (claude 실행 → 브라우저 로그인)
■ 프로젝트 폴더 생성 (mkdir korean-quant && cd korean-quant)
■ 가상환경 생성 및 활성화 (python -m venv venv)
■ 폴더 구조 초기화 (docs/01_environment.md 스크립트 사용)
■ requirements.txt 작성 및 설치 (pip install -r requirements.txt)
■ .env 파일 생성 (docs/01_environment.md 참고)
■ .gitignore 설정
■ Git 초기화 및 첫 커밋
■ CLAUDE.md 작성 (최상위 루트)
■ config/logging_config.py 작성
■ 환경 검증: python -c "import pandas, pykrx, requests; print('OK')"
```

---

## Phase 2 — 데이터 파이프라인 (3~5일) ✅ 완료

```
■ data/collector.py 작성 (multi-tier: KRX Open API → DART → pykrx 폴백)
  ■ KRXDataCollector.get_universe() — KRX Open API 또는 pykrx 폴백
  ■ KRXDataCollector.get_ohlcv()   — SQLite 캐시 + pykrx 개별 조회
  ■ KRXDataCollector.get_fundamentals_all() — KRX API + DART 폴백
  ■ KRXDataCollector.get_market_cap()       — KRX API 또는 pykrx 폴백
  ■ KRXDataCollector.prefetch_daily_trade() — KRX API 벌크 OHLCV + 시가총액
  ■ KRXDataCollector.get_avg_trading_value() — 벡터화 벌크 유동성 조회
  ■ KRXDataCollector.get_suspended_tickers() — 거래정지 종목 감지
  ■ ReturnCalculator.get_returns_for_universe() — DB 프리페치 + 개별 폴백

■ data/dart_client.py 작성 (DART OpenAPI 연동)
  ■ DartClient.get_fundamentals_for_date() — 재무제표 기반 PER/PBR 계산
  ■ DartClient.get_dps_for_tickers() — 주당배당금 조회

■ data/processor.py 작성
  ■ DataProcessor.clean_fundamentals()  — 이상치·결측치 처리
  ■ DataProcessor.filter_universe()     — 유니버스 필터 (유동성, 거래정지, 시총, 금융주)

■ data/storage.py 작성 (SQLite CRUD, WAL 모드, 벌크 쿼리, 999변수 제한 처리)
■ 전체 유니버스 데이터 수집 및 캐시 동작 확인
```

---

## Phase 3 — 팩터 구현 (3~5일) ✅ 완료

```
■ factors/value.py 작성 (ValueFactor.calculate)
  ■ PBR 스코어 검증: 0 이하 제거, 역수 변환, 순위 정규화
  ■ PER 스코어 검증: 적자 제외 확인
  ■ DIV 스코어 검증

■ factors/momentum.py 작성 (MomentumFactor.calculate)
  ■ 12개월 수익률: skip_months=1 적용 확인 (최근 1개월 제외)
  ■ 복합 모멘텀: 12M(60%) + 6M(30%) + 3M(10%)
  ■ 수익률 Winsorize 검증

■ factors/quality.py 작성 (QualityFactor.calculate)
  ■ ROE(40%) + Earnings Yield(30%) + 배당(30%) + 부채비율(선택 20%)
  ■ NaN-aware 가중 합산 (종목별 가용 가중치 정규화)
  ■ 자본잠식(BPS ≤ 0) 제거 확인

■ factors/composite.py 작성 (MultiFactorComposite)
  ■ union 기반 합산 (min_factor_count=2, 2/3 이상 팩터 필요)
  ■ NaN-aware 가중치 재분배
  ■ 가중치 합 = 1.0 검증 (FactorWeights.__post_init__)
  ■ 시가총액 필터 동작 확인
  ■ 금융주 제외 동작 확인

■ tests/test_factors.py 작성 및 통과
```

---

## Phase 4 — 백테스트 (3~5일) ✅ 완료

```
■ backtest/engine.py 작성
  ■ KRX 영업일 캘린더 기반 리밸런싱 (config/calendar.py)
  ■ T+1 시가 체결 (선견 편향 방지)
  ■ 거래 비용 반영 (수수료 + 세금 + 슬리피지, 방향별 차등)
  ■ 벌크 OHLCV 프리페치로 속도 최적화
  ■ 턴오버 추적 (리밸런싱별 교체율 기록)

■ backtest/metrics.py 작성
  ■ CAGR, MDD, 샤프, 칼마, 승률, 변동성 계산 검증

■ backtest/report.py 작성
  ■ quantstats HTML 리포트 생성 (pandas 3.x 호환 패치 포함)
  ■ KOSPI 벤치마크 대비 성과 포함

■ run_backtest.py 작성 (CLI: --mode, --cash 옵션)

□ In-Sample / Out-of-Sample 성과 검증 (Phase 7 진입 전 수행 예정)
```

---

## Phase 5 — 키움 REST API 연동 (3~5일) ✅ 완료

```
■ 키움증권 계좌 개설 및 API 사용 신청
■ 포털에서 허용 IP 등록
■ 모의투자 계정 신청
■ .env 파일에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_ACCOUNT_NO 입력
■ IS_PAPER_TRADING=True 확인

■ trading/kiwoom_api.py 작성
  ■ OAuth2 토큰 자동 갱신 (만료 10분 전)
  ■ Rate Limiting (0.2초 간격)
  ■ 현재가/잔고/미체결 조회
  ■ 매수/매도/취소 주문
  ■ 종목 검색

■ trading/order.py 작성
  ■ 매도 → 체결 확인 → 매수 순서
  ■ 잔고 검증 (BalanceValidationError)
  ■ 턴오버 제한 (TurnoverLimitExceeded)
  ■ MDD 서킷 브레이커 (DrawdownCircuitBreaker)
  ■ 단일 종목 비중 제한 (max_position_pct)
  ■ 고점 가치 JSON 영속화

□ 1개월간 모의투자 페이퍼 트레이딩 관찰 (진행 예정)
```

---

## Phase 6 — 자동화 & 모니터링 (2~3일) ✅ 완료

```
■ 텔레그램 봇 생성 및 설정 완료

■ notify/telegram.py 작성
  ■ send() — 4096자 자동 분할, 429 에러 재시도
  ■ 지수 백오프 재시도 (최대 3회)

■ strategy/screener.py 작성 (MultiFactorScreener)
  ■ 전체 파이프라인: 수집→정제→필터→스코어→선정
  ■ Fundamentals 없을 시 모멘텀 전용 폴백 모드

■ scheduler/main.py 작성
  ■ APScheduler + KRX 캘린더 통합 (config/calendar.py 기반 한국 공휴일 인식)
  ■ 08:50 월말 리밸런싱 / 15:15 일별 방어 체크 (MDD 서킷브레이커 + 트레일링 스톱) / 15:35 일별 리포트
  ■ --dry-run, --now (즉시 리밸런싱), --screen-only (스크리닝만) 모드 지원
  ■ 리밸런싱: 잔고 검증 → 서킷브레이커 재진입 → 스크리닝 → 시장 레짐 → 변동성 타겟팅 → 주문

■ dashboard/app.py 작성
  ■ Streamlit KPI + 보유종목 + 손익 차트
  ■ 60초 캐시로 API 호출 최소화

■ 전체 시스템 통합 테스트 완료
```

---

## Phase 7 — 실전 투입 (진행 중)

```
□ IS_PAPER_TRADING=False 변경 전 최종 확인
  □ 백테스트 성과 목표 달성 여부 재확인
  □ 모의투자 1개월 결과 검토
  □ 비상 중단 방법 숙지 (스케줄러 종료, 수동 전량 매도)

□ 소액 (50~100만원) 실전 투입
□ 실전 vs 백테스트 결과 비교 (매월 기록)
□ 이상 동작 알림 임계값 설정
□ 3개월 관찰 후 비중 확대 여부 결정
□ 전략 개선 사항 기록 (CLAUDE.md 업데이트)
```

---

## 발견된 원본 코드 버그 전체 목록

| # | 위치 | 버그 내용 | 수정 내용 |
|---|------|----------|----------|
| 1 | `scheduler/main.py` | `run_daily_report`에서 존재하지 않는 `KISApiClient()` 호출 | `KiwoomRestClient()` 사용 |
| 2 | `scheduler/main.py` | `schedule` 라이브러리 사용 (requirements에는 `APScheduler`) | `BlockingScheduler` 사용으로 통일 |
| 3 | `scheduler/main.py` | `BMonthEnd` 임포트 `from pandas.tseries.offsets` 불안정 | `pd.offsets.BMonthEnd(0)` 사용 |
| 4 | `scheduler/main.py` | `timezone` 미설정 → 서버 시간 기준으로 동작 위험 | `timezone="Asia/Seoul"` 명시 |
| 5 | `dashboard/app.py` | `KISApiClient` 임포트 (존재하지 않음) | `KiwoomRestClient` 사용 |
| 6 | `dashboard/app.py` | `sys.path` 설정 없음 → 내부 모듈 임포트 실패 가능 | `sys.path.insert()` 추가 |
| 7 | `backtest/engine.py` | `freq="BME"` deprecated (pandas 2.2+) | `pd.offsets.BMonthEnd()` 사용 |
| 8 | `data/collector.py` | pykrx 컬럼명 한글 → 영문 rename 미처리 | `OHLCV_COLUMNS` 매핑 딕셔너리 추가 |
| 9 | `data/collector.py` | 모멘텀 수익률: `iloc[-22]` 하드코딩 (거래일 변동 무시) | `relativedelta`로 정확한 날짜 계산 |
| 10 | `config/settings.py` | `FactorWeights` 가중치 합 검증 없음 | `__post_init__`에서 합 = 1.0 assert 추가 |
| 11 | `requirements.txt` | `vectorbt`, `backtrader`, `pyfolio-reloaded` 포함 → 의존성 충돌 가능 | 제거, pandas 기반 자체 엔진 사용 |

---

## Claude Code CLI 활용 가이드

### 효과적인 작업 요청 예시

```
# 팩터 구현
"docs/04_factors.md를 읽고 factors/value.py를 구현해줘.
 ValueFactor.calculate() 메서드만 먼저 작성해줘."

# 백테스트 디버깅
"backtest/engine.py를 실행했을 때 KeyError가 발생해.
 pykrx 응답 컬럼명을 확인하고 수정해줘."

# 테스트 작성
"factors/value.py의 ValueFactor.calculate()에 대한
 pytest 단위 테스트를 tests/test_factors.py에 작성해줘.
 mock 데이터를 사용해서 PBR 스코어가 올바른지 검증해줘."
```

### CLAUDE.md 업데이트 전략

개발 진행하면서 CLAUDE.md 하단 `현재 개발 진행 상태` 섹션을 매 Phase마다 업데이트해요.

```markdown
## 현재 개발 진행 상태
- [x] Phase 1: 환경 구축
- [x] Phase 2: 데이터 파이프라인 (2025-03-15 완료)
- [ ] Phase 3: 팩터 구현 (진행 중 — value.py 완료, momentum 작업 중)
- [ ] Phase 4: 백테스트

## 알려진 이슈
- pykrx get_market_fundamental 응답 컬럼명이 버전에 따라 다름
  → print(fundamentals.columns) 로 확인 후 OHLCV_COLUMNS 매핑 업데이트
```
