# Changelog

본 프로젝트의 주요 변경 사항. [Keep a Changelog](https://keepachangelog.com/) 형식.

---

## [Unreleased]

### 추가 — Phase 1 마무리: 호가단위 + Sanity Report + Runbook (2026-05-13)

- **feat(trading): KRX 호가단위 처리 (E1)**
  - `trading/tick_size.py` 신규 — `tick_size()` / `round_to_tick(direction)`
    가격대 7단계 호가단위 (1·5·10·50·100·500·1000원) 반영
  - `backtest/engine.py` `_execute_trades` — 매수 시 올림, 매도 시 내림으로 보수적 체결가 시뮬레이션 (시장충격 적용 후 호가단위 정렬)
  - 슬리피지 0.1% 유지 (호가단위 외 체결 불확실성 흡수)
  - 단위 테스트 19 케이스 (`tests/test_tick_size.py`)
- **feat(screener): 리밸런싱 Sanity Report (E3)**
  - `strategy/screener.py` `generate_sanity_report()` 신규 — 선정 종목별 PBR/PER/영업이익/부채비율/섹터/Composite 마크다운 요약
  - 자동 플래그: 부채비율 > 300%(또는 표본 ≥10 시 상위 10%), 영업이익 결측·음수, 섹터=기타, PBR>3, composite 하위 20%(표본 ≥10 시)
  - `backtest/engine.py` `_emit_sanity_report()` — 리밸런싱 시 자동 호출, `docs/reports/sanity_{date}.md` 저장
  - `_calc_portfolio_with_buffer` 반환 시그니처 변경: `list[str]` → `tuple[list[str], pd.DataFrame]` (호출자 1곳만, 영향 격리)
  - `config/settings.py` `SanityReportConfig` 추가 (enabled / save_to_file / telegram, 모두 기본 True)
  - `config/config.yaml` `monitoring.sanity_report` 섹션 추가
  - 단위 테스트 3 케이스 (`TestSanityReport`)
- **docs: 운용 Runbook 작성 (E5)**
  - `docs/RUNBOOK.md` 신규 — 일간/주간/월간/분기 운용 루틴, 비정기 이벤트 대응 (관리종목·급락·장애), 절대 금지 사항 8개
- 검증: 전체 테스트 529개 통과 (38분), 백테스트 회귀 없음

### 변경 — S4 채택 (2026-05-13)

- config: sector_diversification_enabled 3개 프리셋 활성화 (S4 채택, max_sector_count=4)
- POLICY.md: S4 채택 이력 + #4 예외 조항 (섹터 분산 등 종목 교체 목적 변경은 위양성 분석으로 대체 평가)
- 금융주 제외는 별도 config 불필요 — screener가 stock_sector 자동 감지

### 추가 — S4-A 정리 + S4-B 섹터 분산 제약 (2026-05-13)

- **fix(sectors): is_financial 종목명 휴리스틱 기준으로 재정렬**
  - `scripts/fix_financial_classification.py` 신규 — KSIC 64/66 매핑으로 잘못 is_financial=True가 된 지주회사/투자업체를 휴리스틱 미매칭 시 False로 복원
  - 결과: 125 → **56종목**, 2,115행 UPDATE
- **verify_finance_exclusion_impact 재실행 (56종목 기준)**
  - ΔCAGR = **-0.51%p** (이전 125종목 기준 -1.78%p에서 크게 개선)
  - 금융주 32분기 합계 선정: **30건** (이전 245건의 1/8)
  - POLICY 조건 1 (-1%p 이내) 통과 → 금융주 제외 자동 적용 OK
- **feat(screener): 섹터 분산 제약 (S4-B)**
  - `strategy/screener.py`: `_select_with_sector_diversification()` 신규
    composite_score 내림차순으로 후보를 순회하면서 섹터당 max_sector_count 제한.
    "기타"(매핑 없음) 섹터는 면제. select_top 호출 두 곳 모두 통합.
  - `config/settings.py`/`config.yaml`: `UniverseConfig` 4 필드 추가 (기본 OFF)
    sector_diversification_enabled / max_sector_count / max_sector_pct / sector_exempt_names
  - 캐시 키에 2 필드 확장 (enabled, max_count)
  - 단위 테스트 4 케이스 (`TestSectorDiversification`)
  - `scripts/backtest_sector_diversification_s4.py` — A/B(max=4)/C(max=3)/D(max=5) 비교
- **S4-B 백테스트 결과 (2017-2024 KOSPI 프리셋A)**
  - A (OFF): CAGR 6.36% / Sharpe 0.240 / HHI 2025 / 최대섹터 36.4%
  - **B (max=4)**: CAGR 6.80% (+0.44%p), Sharpe 0.259 (+0.019), HHI 1191, 최대섹터 20.0%
  - C (max=3): CAGR 6.11%, Sharpe 0.229 (alpha 손실)
  - D (max=5): CAGR 6.57%, Sharpe 0.250
  - **판정**: 자동 ❌ 미채택 (겹침률 73.4%로 조건 4 미달). 그러나 B는 alpha 동시 개선 + HHI 41% 감소 + 위양성 +1.07%(매우 작음) → 사용자 검토 필요
  - 보고서: `docs/reports/sector_diversification_s4_analysis.md`

### 추가 — S4-A 보강: DART 기업개황 KSIC + baseline 검증 (2026-05-12)

- **feat(dart): 기업개황 API → 업종코드 수집**
  - `data/dart_client.py`: `fetch_company_info()` + `fetch_sector_batch()` 신규
  - `factors/composite.py`: `KSIC_TO_SECTOR` 매핑 (KSIC 상위 2자리 → 투자용 섹터 20개) + `classify_by_ksic()` 함수
  - `scripts/backfill_sectors.py`: `--use-dart-company` 옵션 추가, DART KSIC + 종목명 휴리스틱 합집합 적용
  - 백필 결과: 1,134종목 DART 조회 → 매핑 적용. 매핑 실패 코드 자동 로깅
- **금융주 제외 baseline 영향 측정 (verify_finance_exclusion_impact.py)**
  - 신규 스크립트로 Step 1 + Step 3v2 활성 상태에서 `exclude_finance` ON/OFF 비교
  - **⚠️ 중대한 발견**: 금융주 제외 시 CAGR -1.78%p, Sharpe -0.083, MDD -3.59%p 손실
  - 금융주 32분기 합산 선정: 245건 (분기당 평균 ~7.7개)
  - 원인 추정: KSIC 64/66 매핑이 광범위 (지주회사 다수 포함 → 정상 사업회사를 금융주로 오분류)
  - **운용 결정 필요**: 종목명 휴리스틱만 사용(58종목) vs KSIC 활용(125종목) vs 수동 화이트리스트 정제
  - 보고서: `docs/reports/finance_exclusion_impact_analysis.md`
- 단위 테스트: `TestClassifyByKsic` 6 케이스 추가

### 추가 — S4-A 섹터 인프라 + 금융주 제외 수정 (2026-05-12)

- **fix(screener): 백테스트에서 금융주 제외 미작동 문제 해결**
  - `engine.py` → `screener.screen()` 호출 시 `finance_tickers=None`이 전달되어 PRD 위반 (금융주 제외 무효화)되던 문제
  - `screener.screen()`이 `settings.universe.exclude_finance=True`이고 외부 주입 없을 때 `storage.get_finance_tickers(date)` 자동 호출하도록 수정
  - engine.py 변경 0줄, screener에서 자체 해결
- **feat(data): 섹터(업종) 인프라**
  - `data/storage.py`: `StockSector` 테이블 신규 + `upsert_stock_sectors`/`load_stock_sectors`(PIT 180일 폴백)/`get_finance_tickers`
  - `data/collector.py`: `get_stock_sectors()` 신규 — KRX Open API 응답에서 종목명 추출 후 휴리스틱 매칭 (pykrx 폴백)
  - `factors/composite.py`: `FINANCE_SECTORS` 확장 + `FINANCIAL_NAME_PATTERNS` + `FINANCIAL_TICKER_WHITELIST` + `classify_financial_by_name()` 휴리스틱 함수
    - 배경: KRX 2025-12-27 이용약관 변경으로 KRX/pykrx/FDR 섹터 API 모두 차단됨
    - 종목명 키워드: 은행/증권/보험/생명/화재/카드/캐피탈/파이낸셜/금융지주
    - 화이트리스트: 신한지주/하나금융지주/우리금융지주/카카오뱅크 등 (키워드 미매칭 보강)
  - `scripts/backfill_sectors.py` 신규 — 분기말 영업일 32개 백필
  - 백필 결과: 32분기 × 961종목 = 29,527행 / **금융주 58종목** (KOSPI 2024-12-30 기준: 증권 29 + 보험 15 + 금융업 11 + 은행 3)
- 단위 테스트 추가: `TestClassifyFinancialByName` 6 케이스 + `TestStockSector` 5 케이스

### 추가 — S2 부채비율 상한 필터 (실험, 미채택)

- **feat(data): S2 debt/equity columns + filter (미채택)**
  - `data/dart_client.py`: `TOTAL_LIABILITIES_ACCOUNT_NAMES` 신규 + `_extract_financial_items` 반환 튜플 7→8 (부채 추가) + `get_fundamentals_for_date` 결과에 `TOTAL_EQUITY`/`TOTAL_LIABILITIES`/`DEBT_RATIO` 3 컬럼 추가
  - `data/storage.py`: `Fundamental` 테이블에 nullable 컬럼 3개 추가 (DB_SCHEMA_POLICY 준수) + 마이그레이션
  - `factors/quality.py`: `apply_debt_ratio_filter()` 신규 — 부채비율 상한 + 자본잠식 차단
  - `config/settings.py`/`config.yaml`: `debt_ratio_filter_enabled` (기본 OFF) 등 3 필드
  - `strategy/screener.py`: Step 3 직후 호출 + 캐시 키 3 필드 확장
  - `scripts/backfill_debt_ratio.py` 신규 — 기존 fundamental 행에 BS 데이터 백필 (idempotent)
  - `scripts/backtest_debt_ratio_s2.py` 신규 — 4모드 (A/B-200/C-300/D-400) 비교
  - `docs/reports/debt_ratio_s2_analysis.md` 자동 생성
  - 단위 테스트: `TestDebtRatioFilter` 4 케이스 + `test_save_debt_ratio_columns`
  - **판정: ❌ 미채택** — 3 임계값 모두 5조건 미달:
    - 005620은 이미 Step 3 변형(2)에서 차단됨 → 추가 폐지 회피 효과 없음
    - 위양성 분석: B/C/D 모두 추가 제거 종목 평균 수익률 +3.14% ~ +4.48% (alpha 손실)
    - 종목 겹침률 67.8%/77.4%/81.7% 모두 90% 미달
    - 2022-2024 구간 ΔCAGR -2.03/-2.29/-3.86%p 모두 -2%p 미달
  - 부채비율 필터는 한국 KOSPI에서 부적합 — S4(섹터 중립화)로 진행 권고

### 추가 — DB 스키마 변경 정책 (2026-05-12)

- docs: DB 스키마 변경 정책 (dev/prod 분리 워크플로우)
  - `docs/DB_SCHEMA_POLICY.md` 신규 — 추가만 허용, 삭제/타입변경 금지
  - `CLAUDE.md` 에 한 줄 요약 + 링크 추가

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
