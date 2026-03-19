# 개발 워크플로우 가이드 — /team:team + /team:ralph-loop 하이브리드

이 프로젝트는 문서화가 매우 상세하여(docs/01~08, PRD.md, CLAUDE.md)
대부분의 Phase를 `/team:team`으로 처리할 수 있습니다.

`/team:ralph-loop`은 **런타임 버그가 발생하기 쉬운 Phase 4(백테스트)**에만 집중 투입합니다.

---

## 전체 실행 순서

```
Phase 1: /team:team 환경 구축              ← 1회 실행으로 충분
    ↓
Phase 2: /team:team 데이터 파이프라인       ← 문서에 스펙 상세, 1회 실행 가능
    ↓
Phase 3: /team:team 팩터 구현              ← 수학 공식 명확, 1회 실행 가능
    ↓
Phase 4: /team:ralph-loop 백테스트         ← 런타임 버그 가능성 높음, 반복 교정 필요
    ↓
Phase 5: /team:team 키움 API 연동          ← API 스펙 명확, 1회 실행 가능
    ↓
Phase 6: /team:team 자동화 & 모니터링       ← 정형화된 통합 작업
    ↓
최종:    /team:team 통합 검증 + 보안 검토
```

---

## Phase 1: 환경 구축 (`/team:team`)

```bash
/team:team "프로젝트 초기 환경을 구축해줘.
docs/01_environment.md와 docs/02_architecture.md를 참고해서:
1. 폴더 구조 전체 생성 (data/, factors/, strategy/, backtest/, trading/, notify/, dashboard/, scheduler/, tests/, notebooks/, config/, logs/)
2. 각 폴더의 __init__.py 생성
3. requirements.txt 작성 및 pip install
4. .env.example 생성 (KIWOOM_APP_KEY, KIWOOM_APP_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
5. .gitignore 설정 (.env, venv/, __pycache__/, *.db, logs/, .idea/)
6. config/settings.py — docs/02_architecture.md의 코드 그대로 구현
7. config/logging_config.py — docs/02_architecture.md의 코드 그대로 구현
8. git init 및 첫 커밋
9. 환경 검증: python -c 'import pandas, pykrx, requests, sqlalchemy; print(\"OK\")'
" --mode auto
```

**`/team:team`을 쓰는 이유**: 파일 생성만 하는 단순 작업. Bootstrapper + Security(의존성 감사)가 자동 활성화.

---

## Phase 2: 데이터 파이프라인 (`/team:team`)

```bash
/team:team "데이터 파이프라인을 구현해줘.
docs/03_data_pipeline.md를 참고하고, docs/CLAUDE.md의 알려진 이슈도 반영해서:

1. data/collector.py
   - KRXDataCollector: get_universe(), get_ohlcv(), get_fundamentals_all(), get_market_cap()
   - ReturnCalculator: get_momentum_return(), get_returns_for_universe()
   - pykrx 한글 컬럼명 → 영문 rename 매핑 필수
   - pykrx API 호출 실패 시 재시도 (3회, 지수 백오프)
   - 모멘텀 수익률: relativedelta 사용 (iloc 하드코딩 금지)

2. data/processor.py
   - DataProcessor: clean_fundamentals(), filter_universe()
   - Winsorize 상위 1%, PBR/PER 0 제거, DIV 음수 제거
   - 시가총액 하위 10% 제외, 금융주 제외, 상장 1년 미만 제외

3. data/storage.py
   - SQLAlchemy ORM 모델 (DailyPrice, Fundamental, FactorScore, Portfolio, Trade)
   - DataStorage: save/load, upsert 지원
   - config/settings.py의 DB_PATH 참조

4. tests/test_collector.py, tests/test_processor.py, tests/test_storage.py
   - 단위 테스트 작성 및 통과 확인

모든 함수에 타입 힌트, logging.getLogger(__name__) 사용, print() 금지.
" --mode auto
```

**`/team:team`을 쓰는 이유**: docs/03에 메서드 시그니처, 컬럼 매핑, 버그 수정 사항까지 문서화됨. Backend + QA + Security 역할이 자동 개입.

---

## Phase 3: 팩터 구현 (`/team:team`)

```bash
/team:team "멀티팩터 스코어링 엔진을 구현해줘.
docs/04_factors.md를 참고해서:

1. factors/value.py — ValueFactor.calculate()
   - PBR(0.5) + PER(0.3) + DIV(0.2) 가중 합산
   - PBR/PER: 역수 변환 후 순위 스코어 (0~100)
   - 적자 기업(PER <= 0) 제외

2. factors/momentum.py — MomentumFactor.calculate()
   - 12개월 수익률, 최근 1개월 제외
   - Winsorize 상하위 1%, 순위 기반 정규화

3. factors/quality.py — QualityFactor.calculate()
   - ROE(0.6) + 부채비율 역수(0.4)
   - ROE = EPS / BPS * 100, 자본잠식(BPS <= 0) 제외
   - ROE 범위: -50% ~ +100% 클리핑

4. factors/composite.py — MultiFactorComposite.calculate()
   - Value(0.4) + Momentum(0.4) + Quality(0.2) 합산
   - 3개 팩터 모두 유효한 종목만 포함 (교집합)
   - 상위 30개 종목 선정, 동일 비중

5. strategy/screener.py — MultiFactorScreener.screen()
   - 유니버스 조회 → 수집 → 전처리 → 팩터 계산 → 상위 30개 반환
   - 에러 발생 시 logger.error() + 빈 결과 반환

6. tests/test_factors.py, tests/test_screener.py
   - 각 팩터별 단위 테스트 + 스크리너 통합 테스트

config/settings.py의 FactorWeights, ValueWeights, UniverseConfig, PortfolioConfig 참조.
모든 함수에 타입 힌트 필수.
" --mode auto
```

**`/team:team`을 쓰는 이유**: 수학 공식이 명확하고 엣지 케이스도 문서에 정리됨. 1회 실행으로 충분.

---

## Phase 4: 백테스트 (`/team:ralph-loop`) — 반복 교정 필요

백테스트는 **실행해봐야 발견되는 미묘한 버그**가 많습니다:
- `pd.offsets.BMonthEnd()`의 특정 월 동작
- T일 신호 → T+1 체결의 날짜 정렬
- 거래 비용 방향별 적용 (매도세 vs 매수수수료)
- quantstats 벤치마크 날짜 불일치

### 4-1. 백테스트 엔진 (backtest/engine.py)

```bash
/team:ralph-loop "docs/05_backtest.md를 참고하여 backtest/engine.py를 TDD로 구현해라.

구현 대상:
- MultiFactorBacktest 클래스
- run(start_date, end_date): 월별 리밸런싱 백테스트 실행
  1. 리밸런싱 날짜 생성: pd.offsets.BMonthEnd() (freq='BME' 사용 금지)
  2. T일 팩터 계산 (월말 영업일)
  3. T+1 체결 (다음 영업일 시가, pd.offsets.BDay(1)) — 선견 편향 방지
  4. 거래 비용 반영
     - 매도: 수수료(0.015%) + 세금(0.18%) + 슬리피지(0.1%)
     - 매수: 수수료(0.015%) + 슬리피지(0.1%)
  5. 일별 포트폴리오 가치 기록 (pd.bdate_range)

- strategy/rebalancer.py — Rebalancer 클래스
  - 현재 포트폴리오 vs 목표 포트폴리오 비교
  - 매도/매수 주문 목록 생성

검증 항목:
- 리밸런싱 날짜가 실제 영업일인지 확인
- T+1 체결이 정확히 다음 영업일인지 확인
- 거래 비용 방향별 적용이 올바른지 확인
- 첫 달/마지막 달 경계 처리

tests/test_backtest.py에 엔진 테스트를 먼저 작성하고 구현해라.
config/settings.py의 TradingConfig 참조.

완료 조건: pytest tests/test_backtest.py::TestEngine -v 전체 통과 시
<promise>ENGINE DONE</promise> 출력" --max-iterations 10
```

### 4-2. 성과 분석 + 리포트 (backtest/metrics.py, report.py)

```bash
/team:ralph-loop "docs/05_backtest.md를 참고하여 backtest/metrics.py와 backtest/report.py를 TDD로 구현해라.

구현 대상:
- PerformanceAnalyzer 클래스 (metrics.py)
  - calculate_cagr(portfolio_values): 연 복합 수익률
  - calculate_mdd(portfolio_values): 최대 낙폭 (목표: -20% 이내)
  - calculate_sharpe(returns, risk_free=0.035): 샤프 비율 (목표: 1.0+)
  - calculate_calmar(cagr, mdd): 칼마 비율
  - calculate_win_rate(returns): 일 승률
  - calculate_volatility(returns): 연환산 변동성
  - summary(): 전체 지표 딕셔너리 반환

- ReportGenerator 클래스 (report.py)
  - generate_html(returns, benchmark_returns, output_path): quantstats HTML 리포트
  - KOSPI 벤치마크 비교 포함 (FinanceDataReader 또는 pykrx로 KOSPI 지수 조회)

- run_backtest.py (프로젝트 루트)
  - CLI 진입점: In-Sample(2015~2020), Out-of-Sample(2021~2024) 백테스트 실행
  - 결과 출력 + HTML 리포트 생성

검증 항목:
- 알려진 수익률 시리즈로 CAGR/MDD/Sharpe 수동 계산값과 비교
- quantstats 리포트가 정상 생성되는지 확인
- 벤치마크 날짜 정렬 불일치 처리

tests/test_backtest.py에 PerformanceAnalyzer 테스트를 먼저 작성.

완료 조건: pytest tests/test_backtest.py::TestMetrics -v 전체 통과 + HTML 리포트 생성 확인 시
<promise>METRICS DONE</promise> 출력" --max-iterations 10
```

**`/team:ralph-loop`을 쓰는 이유**: 날짜 계산, 거래 비용 적용, quantstats 호환성 등 **코드를 실행해봐야 발견되는 버그**가 많음. 테스트 실패 → 자동 수정 반복이 핵심.

---

## Phase 5: 키움 REST API 연동 (`/team:team`)

```bash
/team:team "키움 REST API 연동 모듈을 구현해줘.
docs/06_kiwoom_api.md를 참고해서:

1. trading/kiwoom_api.py — KiwoomRestClient 클래스
   - token 프로퍼티: 자동 갱신 (만료 10분 전 재발급)
   - _issue_token(): POST /oauth2/token (응답 필드: 'token', 'expires_dt' — access_token 아님!)
   - get_current_price(ticker): 현재가 조회
   - buy_stock(ticker, qty, order_type='3', exchange='KRX'): 시장가 매수
   - sell_stock(ticker, qty): 시장가 매도
   - get_balance(): 계좌 잔고 조회
   - ping(): API 연결 확인
   - 모의투자: mockapi.kiwoom.com / 실전: api.kiwoom.com
   - IS_PAPER_TRADING 환경변수로 전환

2. trading/order.py — OrderExecutor 클래스
   - execute_rebalancing(target_portfolio): 리밸런싱 주문
     순서: 매도(예수금 확보) → 잔고 재확인(99% 사용) → 매수(동일 비중)
   - _calculate_orders(): 매수/매도 수량 계산
   - IS_PAPER_TRADING=true 이중 확인 (실전 전환 시 WARNING 로그)

3. tests/test_kiwoom_api.py, tests/test_order.py
   - requests mock으로 단위 테스트
   - 토큰 갱신, 주문 순서, 에러 핸들링 검증

에러 핸들링 + 재시도 로직(3회) 포함. 모든 함수 타입 힌트 필수.
" --mode auto --with backend,security,qa
```

**`/team:team`을 쓰는 이유**: API 스펙이 docs/06에 상세히 정의됨. Security가 API 키 관리, 주문 보안을 자동 검토.

---

## Phase 6: 자동화 & 모니터링 (`/team:team`)

```bash
/team:team "자동화 시스템을 구현해줘.
docs/07_automation.md를 참고해서:

1. notify/telegram.py — TelegramNotifier 클래스 (requests 기반, async 아님)
   - send(message): 기본 메시지
   - send_rebalancing_report(portfolio): 리밸런싱 결과
   - send_daily_report(summary): 일별 리포트
   - send_error(error): 오류 알림
   - 4096자 초과 시 분할 발송
   - 발송 실패 시 logger.error(), 예외 전파하지 않음

2. scheduler/main.py — APScheduler BlockingScheduler 기반 데몬
   - run_monthly_rebalancing(): 08:50 실행, 월말 판정 후 리밸런싱
   - run_daily_report(): 15:35 실행, 일별 수익 리포트
   - is_last_business_day_of_month(): pd.offsets.BMonthEnd() 활용
   - timezone='Asia/Seoul' 필수
   - KiwoomRestClient 사용 (KISApiClient 아님!)
   - argparse로 --dry-run 옵션

3. dashboard/app.py — Streamlit 대시보드
   - KPI 카드: 총 평가금액, 예수금, 보유 종목 수, 총 손익
   - 포트폴리오 테이블: 종목명, 수량, 평균가, 현재가, 손익률, 평가금액
   - 수익 곡선 차트
   - sys.path 설정으로 프로젝트 루트 임포트

4. tests/test_telegram.py, tests/test_scheduler.py
   - requests mock으로 텔레그램 테스트
   - 스케줄러 월말 판정 테스트

리밸런싱 실패 시 텔레그램 에러 알림 필수.
" --mode auto --with backend,qa
```

**`/team:team`을 쓰는 이유**: 텔레그램(HTTP POST), APScheduler(패턴 코드), Streamlit(기본 구조) 모두 정형화된 작업.

---

## 최종: 통합 검증 + 보안 검토 (`/team:team`)

```bash
/team:team "전체 시스템의 통합 테스트와 보안 검토를 수행해줘.

1. tests/test_integration.py 작성
   - E2E 테스트: 데이터 수집 → 전처리 → 팩터 계산 → 스크리닝 → 주문 (mock)
   - 스케줄러 → 텔레그램 알림 흐름 테스트

2. 보안 검토
   - .env 파일이 .gitignore에 포함 확인
   - API 키 하드코딩 전체 소스 스캔 (grep으로 확인)
   - IS_PAPER_TRADING 이중 확인 로직 검증
   - SQL injection 방지 (ORM 사용 확인)
   - 키움 API 토큰 메모리 노출 방지

3. 코드 품질
   - black 포매팅 적용
   - ruff 린트 통과
   - 모든 함수 타입 힌트 확인
   - logging 컨벤션 준수 (print 사용 없는지)

4. pytest 전체 실행 및 결과 보고
" --with qa,security
```

---

## 요약 표

| Phase | 명령 | 방식 | 이유 |
|-------|------|------|------|
| 1. 환경 구축 | `/team:team --mode auto` | 1회 실행 | 파일 생성만, 반복 불필요 |
| 2. 데이터 파이프라인 | `/team:team --mode auto` | 1회 실행 | docs/03에 스펙 상세 |
| 3. 팩터 구현 | `/team:team --mode auto` | 1회 실행 | 수학 공식 명확 |
| **4. 백테스트** | **`/team:ralph-loop`** | **반복 교정** | **런타임 버그 가능성 높음** |
| 5. 키움 API | `/team:team --with backend,security,qa` | 1회 실행 | API 스펙 명확 |
| 6. 자동화 | `/team:team --with backend,qa` | 1회 실행 | 정형화된 패턴 |
| 최종 검증 | `/team:team --with qa,security` | 1회 실행 | 전문가 관점 필요 |

---

## 팁

- 각 Phase 완료 후 `git commit`으로 진행 상황 저장
- `/team:team --mode auto`은 매 단계에서 확인을 요청하므로 중간 수정 가능
- Phase 4의 `/team:ralph-loop`이 max-iterations에 도달해도 미완이면, 프롬프트를 구체화하여 재실행
- Phase 2~3에서 예상외로 테스트 실패가 반복되면, 해당 모듈만 `/team:ralph-loop`으로 전환 가능
- `/team:cancel-ralph`로 언제든 루프 중단 가능
