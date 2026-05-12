# Changelog

본 프로젝트의 주요 변경 사항. [Keep a Changelog](https://keepachangelog.com/) 형식.

---

## [Unreleased]

### 추가 — Step 3-B/C 연속 흑자 4분기 필터 + 백테스트 (미채택)

- **feat(quality): consecutive profit filter (Step 3-B)** — 운용 활성화 보류
  - `factors/quality.py`: `apply_consecutive_profit_filter()` 신규 — Step 3-A의 분기 시계열을 PIT 안전하게 조회하여 최근 N분기 연속 흑자 검증
  - `config/settings.py` `QualityConfig` 5 필드 추가 (`consecutive_profit_*`, 기본 OFF)
  - `config/config.yaml` 프리셋 A/B/C에 5 라인 추가 (모두 false)
  - `strategy/screener.py` Step 1 직후 호출 + 캐시 키 5 필드 확장
  - `tests/test_factors.py` `TestConsecutiveProfitFilter` 5 케이스 (005620 시나리오 포함)
- **script/report: scripts/backtest_quality_filter_step3.py + docs/reports/quality_filter_step3_analysis.md**
  - A(Step1만) vs B(Step1+Step3) 2017-2024 KOSPI 비교 + 위양성 분석
  - **판정: ❌ 미채택** — 005620 회피는 성공했으나:
    - 조건 4 종목 겹침률 70.7% (기준 90% 미달)
    - 조건 5 2020-2022 구간 ΔCAGR=-3.63%p (기준 -2%p 미달)
    - 위양성 평균 수익률 **+18.15%** (n=118) — alpha 손실 명백
  - 변형안 후보 (보고서 명시): n_quarters=2 완화 / require_all_positive=False / KOSPI 외 시장 확장

### 추가 — Step 3-A 분기 재무 시계열 인프라

- **feat(data): quarterly fundamentals storage + DART quarterly series fetcher (Step 3-A)**
  - `data/storage.py`: `FundamentalQuarterly` 테이블 신규 + `upsert_fundamentals_quarterly` + PIT 안전 `load_fundamentals_quarterly`
    - `_pit_end_period`: as_of_date → 시점에 공시된 가장 최근 (bsns_year, reprt_code) 결정 (dart_client._determine_report_period와 동일 lag)
    - `_walk_back_quarters`: 분기 역행 (11011→11014→11012→11013→전년 순환)
  - `data/dart_client.py`: `fetch_quarterly_series()` 신규 — 시작점부터 과거 n분기 일괄 수집 (기존 _fetch_multi_account_batch + _extract_financial_items 재사용)
  - `scripts/backfill_quarterly_fundamentals.py`: 신규 백필 스크립트 (idempotent, --start-year/--end-year/--market)
  - `tests/test_storage.py`: TestFundamentalQuarterly 5 케이스 (upsert/PIT 안전/연도 경계 역행/_pit_end_period 로직)
  - `tests/test_dart_client.py`: TestWalkBackQuarters 3 케이스 + TestFetchQuarterlySeries 2 케이스
  - 이 커밋은 Step 3 인프라만 제공. 필터 함수(3-B) / 백테스트(3-C)는 별도 커밋

### 추가 — 폐지 임박 자동 매도 (opt-in)

- **feat(risk_guard): delisting auto-sell option (default OFF, dry-run first)**
  - `monitor/risk_guard.py`: `execute_delisting_auto_sell()` 메서드 신규 — `check_delisting_imminent` 감지 종목 중 `failure`/`expired`/`other` 카테고리만 시장가 매도. `merger`/`voluntary`는 정상 폐지로 자동매도 대상 아님 (정리매매로 가치 회수).
  - `config/settings.py` `RiskGuardConfig` 4 필드 추가 (`delisting_auto_sell_enabled`/`_categories`/`_dry_run`/`_max_days_until`)
  - `config/config.yaml` `monitoring.risk_guard` 섹션 확장 (기본 OFF, dry_run=true)
  - `monitor/alert.py` `format_delisting_auto_sell_message()` 신규 (dry_run/sold/failed 구분 표시)
  - `scheduler/main.py` `run_risk_guard_delisting` (09:30 Job)에 자동매도 호출 통합
  - `tests/test_risk_guard.py` 7 케이스 추가 (`TestExecuteDelistingAutoSell` + `TestDelistingAutoSellMessage`)
  - `docs/POLICY.md` 다층 방어 구조 표에 4차(opt-in) 행 추가
  - 단계적 활성화 권장: 1주차 `enabled:true + dry_run:true` → 2주차 `dry_run:false`

### 변경 — Step 1 채택 (2026-05-12)

- POLICY.md: 5조건 #2 재정의 — alpha 개선 경로 (b) 추가
- config: operating_quality_filter_enabled 3개 프리셋 활성화 (Step 1 채택)

### 변경 — Step 3 변형 (2) 채택 (2026-05-12)

- config: consecutive_profit_filter 활성화 (Step 3 변형 2 채택, require_all_positive=false)
- POLICY.md: 채택된 방어 장치 + 변경 이력 추가 (5조건 전부 통과)

### 추가 — Step 1 본업 품질 필터

- **feat(quality): operating quality filter (Step 1)**
  - `QualityFactor.apply_operating_quality_filter` 신규 (`factors/quality.py`) — 영업이익/매출/영업CF(PCR 양수) 3단계 양수 필터
  - `config/settings.py` `QualityConfig` 4 필드 추가 (`operating_quality_filter_enabled` 등, 기본 False)
  - `strategy/screener.py` F-Score 필터 직후 호출 + 캐시 키 신규 4필드 반영
  - `config/config.yaml` 프리셋 A/B/C 모두에 4 라인 추가 (모두 false 유지 — 검증 후 사용자 확인 후 활성화)
  - `scripts/backtest_quality_filter_step1.py` — A(OFF) vs B(ON) 2017-2024 비교 백테스트 + POLICY.md 5조건 자동 평가 + 005620 회피 검증
  - `docs/reports/quality_filter_step1_analysis.md` — 백테스트 결과 보고서 자동 생성
  - 단위 테스트 3건 추가 (`tests/test_factors.py::TestOperatingQualityFilter`)
  - 동기: docs/case_studies/005620_lesson.md — 일회성 이익 가치함정 사전 차단 목적

### Milestone — 2026-04-15: 005620 분석 시리즈 완료, 폐지 방어 정책 확정

**결론**: Baseline (현 설정) + `risk_guard` 다층 방어가 최적. 추가 방어 장치 모두 부적합 판정.

#### 분석 시리즈 타임라인

1. **상장폐지 데이터 통합** (KRX KIND 1,926건 임포트)
   - `delisted_stock` 테이블 + 5종 카테고리 (failure/merger/voluntary/expired/other)
   - 2017-2024 범위 failure 144종목

2. **생존자 편향 보정 백테스트**
   - 2017-2024 32회 리밸런싱 → failure 노출 1건 (3.1%) — **005620 단일**

3. **F-Score 필터 효과 검증**
   - 144 failure 중 81개 분석 → 4점 이상 0개 (0.0%) — 필터 효과적

4. **005620 사례 정밀 분해**
   - 2017-06-30 기준 F-Score 4/5 (턱걸이 통과)
   - 반기보고서 공시 직후 EPS -72k → +111k 회계적 급변

5. **Reporting Lag 엄격 적용 실험** (`strict_reporting_lag=True`)
   - 2017-06-30에서 005620 회피 ✅
   - 그러나 전체 CAGR **-12.18%p 부작용** → 원복

6. **대안 방어 장치 4종 비교**
   - EPS 부호 반전 감지: ΔCAGR -7.08%p
   - 거래정지 이력 필터: 005620 회피 실패
   - min_fscore=5: ΔCAGR -12.73%p
   - 통합: ΔCAGR -13.38%p
   - **모두 도입 조건(-1%p 이내) 미충족**

7. **정책 확정**
   - `docs/POLICY.md` — 다층 방어 구조 + 의사결정 5조건
   - `docs/case_studies/005620_lesson.md` — 학습 사례 보존
   - 실험용 옵션은 코드 유지, 기본값 False

#### 다음 검토 시점

**2026-Q4** — 실전 운용 2분기 데이터 누적 후 폐지 노출률 vs 백테스트(3.1%) 검증

---

## [v2.0] — 2026-03-26

### 추가
- **상장폐지 DB 통합** (`delisted_stock` 테이블, KRX KIND 임포트)
- **모니터링 시스템** (`monitor/`) — 일간 스냅샷, 벤치마크, 드리프트, 리스크 감시
- `risk_guard.check_delisting_imminent()` — 보유 종목 폐지 30일 전 알림
- 자동 백필 (`scripts/auto_backfill_missing.py`) — 매일 09:00 누락 복구
- 일별 데이터 수집 시각 16:00 → 16:30 (KRX 업데이트 지연 대응)
- 매수 체결 확인 (`_wait_for_buys_to_settle`) + 재시도 중복 방지

### 변경
- **전략 v2.0 확정** (`docs/PRD_v2.md`)
  - Value: PBR(50) + PCR(30) + DIV(20)  ← PER 제거, PCR 신규
  - Quality: 복합 스코어에서 제거 (Q=0.00), F-Score는 필터로만 유지
  - 프리셋 9개 → 3개 (A 핵심/B 보수/C 공격)
  - 금액 프리셋 7단계 → 4단계 (소/중/대/거)
- Reporting Lag DART 내부 자동 처리 (`_determine_report_period`)
- Walk-Forward 백테스트 추가
- `_estimate_dividend_income()` deprecated (한국 시장 배당 처리 미래 과제)

### 수정
- KRX API 변경(2025-12-27) 대응 — `pykrx-openapi` + multi-tier 폴백
- KOSPI 벤치마크 0.00% 버그 (Naver/KRX OpenAPI 폴백 추가)
- `trailing_stop_pct=null` 안전장치
- pandas 2.2+ `freq="BME"` deprecated → `pd.offsets.BMonthEnd()`

---

## [v1.x] — 2026-01 ~ 2026-03

초기 구현 — 자세한 이력은 `git log` 참조.
