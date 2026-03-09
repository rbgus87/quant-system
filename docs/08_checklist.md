# 08. Phase별 개발 체크리스트

## Phase 1 — 환경 구축 (1~2일)

```
□ Python 3.11 설치 및 버전 확인 (python --version)
□ VSCode + 필수 확장 설치 (Python, Pylance, Jupyter, GitLens)
□ Node.js 18+ 설치
□ Claude Code CLI 설치 (npm install -g @anthropic-ai/claude-code)
□ Claude Code CLI 인증 (claude 실행 → 브라우저 로그인)
□ 프로젝트 폴더 생성 (mkdir korean-quant && cd korean-quant)
□ 가상환경 생성 및 활성화 (python -m venv venv)
□ 폴더 구조 초기화 (docs/01_environment.md 스크립트 사용)
□ requirements.txt 작성 및 설치 (pip install -r requirements.txt)
□ .env 파일 생성 (docs/01_environment.md 참고)
□ .gitignore 설정
□ Git 초기화 및 첫 커밋
□ CLAUDE.md 작성 (최상위 루트)
□ config/logging_config.py 작성
□ 환경 검증: python -c "import pandas, pykrx, requests; print('OK')"
```

---

## Phase 2 — 데이터 파이프라인 (3~5일)

```
□ data/collector.py 작성
  □ KRXDataCollector.get_universe() — 날짜 기준 종목 목록
  □ KRXDataCollector.get_ohlcv()   — 단일 종목 일봉
  □ KRXDataCollector.get_fundamentals_all() — 전체 시장 배치 조회
  □ KRXDataCollector.get_market_cap()       — 시가총액
  □ ReturnCalculator.get_returns_for_universe() — 모멘텀 수익률

□ data/processor.py 작성
  □ DataProcessor.clean_fundamentals()  — 이상치·결측치 처리
  □ DataProcessor.filter_universe()     — 유니버스 필터

□ notebooks/01_data_exploration.ipynb 작성 및 검증
  □ KOSPI 전체 종목 수 확인 (~800~900개)
  □ pykrx 응답 컬럼명 확인 (한글 or 영문)
  □ PBR/PER 분포 시각화 및 이상치 확인
  □ 배치 조회 속도 측정 (vs 개별 조회)

□ data/storage.py 작성 (SQLite CRUD)
□ 전체 유니버스 1년치 데이터 수집 테스트 (속도 확인)
```

---

## Phase 3 — 팩터 구현 (3~5일)

```
□ factors/value.py 작성 (ValueFactor.calculate)
  □ PBR 스코어 검증: 0 이하 제거, 역수 변환, 순위 정규화
  □ PER 스코어 검증: 적자 제외 확인
  □ DIV 스코어 검증

□ factors/momentum.py 작성 (MomentumFactor.calculate)
  □ 12개월 수익률: skip_months=1 적용 확인 (최근 1개월 제외)
  □ 수익률 Winsorize 검증

□ factors/quality.py 작성 (QualityFactor.calculate)
  □ ROE = EPS / BPS × 100 계산 확인
  □ 자본잠식(BPS ≤ 0) 제거 확인

□ factors/composite.py 작성 (MultiFactorComposite)
  □ 공통 종목만 합산 (팩터별 누락 종목 처리)
  □ 가중치 합 = 1.0 검증 (FactorWeights.__post_init__)
  □ 시가총액 필터 동작 확인
  □ 금융주 제외 동작 확인

□ notebooks/02_factor_analysis.ipynb 작성 및 검증
  □ 팩터별 스코어 분포 히스토그램
  □ 팩터 간 상관관계 히트맵
  □ 최종 포트폴리오 30개 종목 확인

□ tests/test_factors.py 작성 및 통과
```

---

## Phase 4 — 백테스트 (3~5일)

```
□ backtest/engine.py 작성
  □ _get_rebalance_dates: pd.offsets.BMonthEnd() 사용 (freq='BME' 아님)
  □ _next_business_day: pd.offsets.BDay(1) 사용
  □ _execute_rebalancing: 거래 비용 (수수료 + 세금 + 슬리피지) 반영 확인
  □ _record_period: pd.bdate_range() 사용

□ backtest/metrics.py 작성
  □ CAGR, MDD, 샤프, 칼마, 승률 계산 검증

□ run_backtest.py 작성

□ In-Sample 백테스트 (2015-01-01 ~ 2020-12-31)
  □ 실행 완료
  □ CAGR 확인 (목표: 15%+)
  □ MDD 확인 (목표: -20% 이내)
  □ 샤프 비율 확인 (목표: 1.0+)
  □ KOSPI 대비 초과수익 확인

□ Out-of-Sample 백테스트 (2021-01-01 ~ 2024-12-31)
  □ In-Sample 대비 성과 저하 폭 확인 (과최적화 여부)

□ backtest/report.py 작성
  □ quantstats HTML 리포트 생성 확인
  □ 수익 곡선 차트 저장 확인

□ notebooks/03_backtest_result.ipynb 작성
```

---

## Phase 5 — 키움 REST API 연동 (3~5일)

```
□ 키움증권 계좌 개설 (비대면 가능)
□ openapi.kiwoom.com → [API 사용신청] 클릭 → 이용 등록
□ 포털에서 허용 IP 등록 (필수! 미등록 시 모든 요청 차단)
□ 모의투자 계정 신청
□ API 가이드 PDF/Excel 다운로드 (응답 필드명 확인)
□ .env 파일에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_ACCOUNT_NO 입력
□ IS_PAPER_TRADING=True 확인

□ trading/kiwoom_api.py 작성
  □ 토큰 발급 테스트 (_issue_token) → "token" 필드 확인
  □ 현재가 조회 테스트 (get_current_price, ka10001)
  □ 잔고 조회 테스트 (get_balance, kt00018)
  □ 모의 매수 주문 테스트 (buy_stock, kt10000, exchange='KRX')
  □ 모의 매도 주문 테스트 (sell_stock, kt10001, exchange='KRX')
  □ 미체결 조회 테스트 (get_unfilled_orders, kt00013)

□ trading/order.py 작성
  □ execute_rebalancing 모의투자 테스트
  □ 매도 → 잔고 재확인 → 매수 순서 확인

□ 실제 응답 기준으로 응답 필드명 수정
  □ cur_prc, ord_no 등 PDF 명세 대조 확인

□ 1개월간 모의투자 페이퍼 트레이딩 관찰
  □ 매월 말 신호 자동 계산 확인
  □ 주문 실행 및 체결 확인
  □ 텔레그램 알림 수신 확인
```

---

## Phase 6 — 자동화 & 모니터링 (2~3일)

```
□ 텔레그램 봇 생성 (BotFather에서 /newbot)
□ 텔레그램 채팅 ID 확인
□ .env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력

□ notify/telegram.py 작성
  □ send() 테스트 메시지 수신 확인
  □ send_rebalancing_report() 형식 확인
  □ send_error() 형식 확인

□ strategy/screener.py 작성 (MultiFactorScreener)

□ scheduler/main.py 작성
  □ APScheduler BlockingScheduler 사용 확인
  □ timezone="Asia/Seoul" 설정 확인
  □ is_last_business_day_of_month() 동작 확인
  □ run_monthly_rebalancing() 수동 실행 테스트
  □ run_daily_report() 수동 실행 테스트

□ dashboard/app.py 작성
  □ streamlit run dashboard/app.py 실행 확인
  □ 잔고 조회 데이터 표시 확인

□ 전체 시스템 통합 테스트 (스케줄러 → 알림 → 대시보드)
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
