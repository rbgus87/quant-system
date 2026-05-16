# Changelog

본 프로젝트의 주요 변경 사항. [Keep a Changelog](https://keepachangelog.com/) 형식.

---

## [Unreleased]

### 수정 — fix: matplotlib 한글 폰트 캐시 불일치 (2026-05-16)

- **config/font.py** `setup_matplotlib_korean_font()`:
  - `fm._load_fontmanager(try_read_cache=False)` 추가 — exe 환경에서 NanumBarunGothic 캐시 참조 방지
  - 폴백 분기 추가 — 한글 폰트 감지 실패 시 Malgun Gothic 강제 설정

### 성능 — perf: exclude unused modules from exe build (2026-05-16)

- **build_exe.py**: 운용 exe에 불필요한 대형 패키지 빌드 제외
  - `--hidden-import=backtest.engine/metrics/report` 3줄 삭제
  - `--exclude-module=backtest/quantstats/seaborn/scipy` 4줄 추가
  - 빌드 시간 단축 및 exe 크기 감소 목적

### 추가 — E6: GitHub Actions CI/CD (2026-05-16)

- **.github/workflows/ci.yml**: push/PR마다 자동으로 ruff + pytest 실행
  - Python 3.12, ubuntu-latest, `pip cache` 적용
  - 더미 API 키 env로 실거래 API 호출 차단 (`IS_PAPER_TRADING=true`)
  - `branches: [main, feature/**]` push 및 `main` PR 트리거
- **ruff.toml**: 린트 설정 추가 (`line-length=200`, E/F/W/I 룰)
  - `gui/**`: E402 제외 (PyQt 설정 후 import)
  - `scripts/**`, `tests/**`: F841/E731 제외 (분석 스크립트 미사용 변수 허용)
- **코드 정리** (ruff auto-fix): 미사용 import 61건, f-string 64건 정리
- **버그 수정**: `test_backtest.py` 4케이스 — `order_qty` → `price, shares` (E2 시그니처 반영)
- **버그 수정**: `scheduler/main.py` — `"pd.DataFrame"` 어노테이션 → `Any` (F821 제거)
- **버그 수정**: `backtest/metrics.py` — `monthly_first` 미사용 변수 제거 (F841)

### 변경 — E2: 종목별 변동성 기반 시장충격 모델 (2026-05-15)

- **strategy/rebalancer.py**: `estimate_market_impact`에 `daily_volatility: Optional[float] = None` 추가
  - `daily_volatility=None`이면 기존 σ=0.01 동작 유지 (하위 호환)
  - 신규: 종목별 σ 전달 시 실제 변동성으로 Square-Root Model 계산
  - `price: float`, `shares: int` 파라미터로 시그니처 명시화
- **backtest/engine.py**: `_execute_trades`에서 리밸런싱마다 종목별 σ 1회 벌크 조회 후 전달
  - `_get_ticker_daily_volatilities()` 신규 (σ_annual/√252 변환)
  - `MultiFactorBacktest(use_ticker_sigma=False)` 플래그로 구버전 동작 재현 가능
  - 조회 실패 시 0.01 폴백 (graceful)
- **scripts/verify_sigma_impact_e2.py**: A(σ=0.01)/B(종목별 σ) 백테스트 비교
- **tests/test_rebalancer.py**: TC-5(하위 호환), TC-6(σ 3배 → impact 3배) 추가

### 변경 — S5 Inverse-Volatility 가중치 채택 (2026-05-15)

- **config**: `portfolio.weighting_method` `"equal"` → `"inverse_vol"` (S5 채택)
- **config**: `portfolio.max_position_pct` `0.15` → `0.10` (C_invvol_10 확정)
- **POLICY.md**: S5 채택 이력 추가 (Sharpe+0.019, Vol -2.8%p, 5조건 전부 통과)
- 종목 선정 로직 변경 없음 — V70M30 + Vol70 유지, 비중 배분만 변경

### 추가 — 백테스트 속도 최적화 3종 (2026-05-15)

- **Opt 1: 일별 가격 백필 스크립트** (`scripts/backfill_daily_prices.py` 신규)
  - KRX Open API로 과거 전종목 OHLCV를 1거래일 = 1 API 호출로 수집
  - 이미 저장된 날짜 자동 스킵 (idempotent), `--start-date`/`--end-date`/`--market` 옵션
  - 백필 후 재실행 시 pykrx API 호출 제거 → 30~60분 → 3~5분 목표

- **Opt 2: 비교 백테스트 병렬 실행** (`backtest/parallel.py` 신규)
  - `run_parallel_backtests(tasks, max_workers=4)`: ProcessPoolExecutor 기반
  - `_backtest_worker`: 모듈 레벨 함수로 picklable, 프로세스별 독립 settings
  - `scripts/backtest_weighting_s5.py` 수정: 기본 병렬 실행, `--sequential` 옵션으로 순차 전환
  - 병렬 실패 시 순차 폴백 자동 적용

- **Opt 3: 분기 재무 프리로드** (`data/storage.py`, `backtest/engine.py` 수정)
  - `storage.load_fundamentals_quarterly_bulk()`: 전종목 분기 데이터를 단일 SQL로 조회
  - `storage.preload_fundamentals_quarterly()` / `clear_fq_preload()`: 리밸런싱별 캐시 수명 관리
  - `load_fundamentals_quarterly()`: 프리로드 캐시 히트 시 DB 쿼리 없이 O(1) 반환
  - `engine._preload_quarter_data()`: 각 리밸런싱 시작 전 프리로드 호출
  - `storage.load_close_matrix()`: 종가 pivot matrix 편의 메서드 추가

- **collector.py**: `get_daily_prices_cached()` 추가 — 멀티 티커 벌크 조회 + 갭 pykrx 보충
- **tests**: `test_storage.py`에 `TestLoadCloseMatrix`, `TestFundamentalsQuarterlyBulkAndPreload` 추가, `tests/test_parallel.py` 신규

### 추가 — S5 포지션 사이징 고도화 (2026-05-15)

- **feat(s5): Equal-Weight → Inverse-Volatility 비중 배분 인프라 구축**
  - `factors/volatility.py` 수정: `get_raw_volatilities()` 신규 (연율화 σ 원본값 반환), `_compute_ann_vol()` 내부 공통 메서드 추출로 코드 중복 제거
  - `strategy/rebalancer.py` 수정: `compute_inverse_vol_rebalance()` 신규 (1/σ 정규화 → cap/재분배 → 주수 계산)
  - `config/settings.py` 수정: `PortfolioConfig` 4필드 추가 (`weighting_method`, `vol_lookback_days`, `max_position_pct`, `min_position_pct`) + validate 확장
  - `config/config.yaml` 수정: `portfolio` 섹션에 4필드 추가 (기본값 `equal`, 60일, 15%, 2%)
  - `backtest/engine.py` 수정: `_execute_trades`에 `inverse_vol` 분기 추가 (`VolatilityFactor.get_raw_volatilities` → `compute_inverse_vol_rebalance`)
  - `strategy/screener.py` 수정: `cache_key`에 `weighting_method` 추가, `_update_inverse_vol_weights()` 신규 (Sanity Report용 weight 업데이트)
  - `tests/test_rebalancer.py` 신규 (4 케이스: 동일σ/2배σ/cap재분배/NaN대체)
  - `tests/test_volatility.py` 수정: `TestGetRawVolatilities` 추가 (2 케이스)
  - `scripts/backtest_weighting_s5.py` 신규 (A_equal/B_invvol/C_invvol_10/D_invvol_20 비교)
  - `docs/reports/weighting_s5_analysis.md` 신규 (스크립트 실행 시 자동 생성)

### 추가 — S7 Low-Volatility 팩터 탐색 (2026-05-15)

- **feat(s7): Low-Volatility 팩터 도입 탐색 + 채택 결정**
  - `factors/volatility.py` 신규: `VolatilityFactor.calc_volatility_score()` (rolling std → 역순위 → 0~100 점수)
  - `factors/composite.py` 수정: `low_vol_score: Optional[pd.Series] = None` backward-compatible 추가
  - `config/settings.py` 수정: `FactorWeights.low_vol: float = 0.00` 추가
  - `config/config.yaml` 수정: 프리셋 A/B/C `factor_weights`에 `low_vol: 0.00` 추가
  - `strategy/screener.py` 수정: VolatilityFactor 통합, `cache_key` 확장
  - `tests/test_volatility.py` + `tests/test_composite_lowvol.py` 신규 (9 케이스)

- **Part 1 — IC/IR 분석** (`scripts/analyze_lowvol_ic.py`)
  - 31분기 × 3 lookback(60/90/120일) 단일 패스
  - 60d: Mean IC=0.092, **IR=3.89**, Hit Rate=80% → **PROCEED 판정**

- **Part 2 — 가중치 백테스트** (`scripts/backtest_lowvol_weights_s7.py`)
  - 5가지 조합(A_baseline/B_V70L30/C_V60L40/D_V50M20L30/E_V100) 전기간 백테스트
  - 결과: Low-vol 추가 시 모든 조합에서 Sharpe·CAGR·DSR 저하
  - A_baseline: CAGR=6.30%, Sharpe=0.272, DSR=0.729
  - 최고후보 E_V100(Sharpe=0.229): POLICY 2/5 통과
  - **결론: 현행 Preset A (V70M30) 유지 — Low-vol 팩터 채택 불가**

- `docs/reports/lowvol_ic_s7_analysis.md` 신규 (IC/IR 보고서)
- `docs/reports/lowvol_factor_s7_analysis.md` 신규 (백테스트 + POLICY 평가 보고서)

---

### 추가 — 팩터 IC/IR/Quintile Decay 분석 V3 (2026-05-15)

- **analysis(factor-ic): 팩터 예측력 정량 분석 스크립트 신규**
  - `scripts/analyze_factor_ic_v3.py` 신규
  - Value/Momentum/Quality 하위 지표 포함 11개 팩터 IC/IR/Hit Rate 측정 (31분기)
  - 유니버스: F-Score≥4, Step1/3·S4 비활성 (팩터 순수 예측력 측정)
  - Reporting Lag 적용 (재무 기준일: 전년/전전년 12/31)
  - Quintile Decay (Q1~Q5 평균 분기 수익률 + Spread + Monotonic 검증)
  - 연도별 IC 시계열 (2017-2024)
  - 핵심 발견: Value가 Alpha 주요 원천 (IR=+0.572, Hit Rate=81%), Momentum/Quality IR 음수
- `tests/test_factor_analysis.py` 신규 (7 케이스: IC 완벽상관·랜덤·분포 + Quintile 방향성·NaN)
- `docs/reports/factor_ic_v3_analysis.md` 신규 (스크립트 실행 시 자동 생성)

### 추가 — DSR + 통계적 유의성 검정 V2 (2026-05-15)

- **analysis(dsr): Deflated Sharpe Ratio 분석 스크립트 신규**
  - `scripts/analyze_deflated_sharpe_v2.py` 신규
  - PSR (Probabilistic Sharpe Ratio, Bailey & López de Prado 2014) — 비정규 보정
  - DSR (Deflated Sharpe Ratio) — N_trials=20 다중 시행 선택 편향 보정
  - t-statistic (Opdyke 2007 비정규 보정식), MinTRL (vs SR*=0, vs KOSPI)
  - 핵심 결과: DSR=0.729(⚠️), PSR=0.770, t=0.744(p=0.228), MinTRL≈37.5년
  - 종합 판정: **유의하지 않지만 양의 신호** (DSR 0.50~0.95) — 8년 데이터는 구조적 부족
  - V3 연계 해석: Momentum IR=-0.057(✗) → Sharpe 하방 압력; Value 단독 시나리오 검토 권장
  - `universe_guard()` 없이 Preset A 직접 실행 — engine.py·config.yaml 변경 없음
- `docs/reports/deflated_sharpe_v2_analysis.md` 신규 (스크립트 실행 시 자동 생성)

### 추가 — 랜덤 벤치마크 100회 시뮬 V5 (2026-05-14)

- **analysis(random): 랜덤 벤치마크 스크립트 신규**
  - `scripts/analyze_random_benchmark_v5.py` 신규
  - 동일 유니버스(Step1+Step3v2+F-Score≥4)에서 무작위 동일가중 20종목 100회 시뮬
  - 전략 CAGR(6.46%) → 랜덤 분포 84%ile, Sharpe(0.245) → 82%ile
  - 판정: **alpha 존재 가능** (75~95%ile) — 팩터 기여 +3.51%p CAGR, +0.151 Sharpe
  - `universe_guard()` context manager로 S4 비활성 + n_stocks=9999 임시 적용 — engine.py 변경 없음
  - 분기별 종가 close-to-close 수익률, 왕복비용 0.38% 고정 차감 (100% 턴오버 가정)
  - 95%CI: CAGR [-1.54%, 9.23%], Sharpe [-0.125, 0.326]
- `docs/reports/random_benchmark_v5_analysis.md` 신규 (스크립트 실행 시 자동 생성)

### 추가 — 거래비용 Sensitivity 분석 V4 (2026-05-14)

- **analysis(cost): 거래비용 민감도 분석 스크립트 신규**
  - `scripts/analyze_cost_sensitivity_v4.py` 신규
  - 슬리피지(시장충격)를 5단계 (~5/15/20/30/50bp)로 변화시키며 CAGR·Sharpe·MDD·Sortino 측정
  - `Rebalancer.estimate_market_impact`를 외부 패치(monkeypatch)로 고정값으로 대체 — `engine.py` 변경 없음
  - Step1 + Step3v2 + S4 모두 ON (Preset A), KOSPI 2017-2024, seed=42
  - 무너지는 지점(Sharpe<0, CAGR<0%) 선형 보간 + 자동 판정("실거래 가능" / "위험")
  - 분기 턴오버·연간비용 부담·보유기간 추가 출력
  - KOSPI Buy-and-Hold 벤치마크 비교 (FinanceDataReader 1차 / kospi_index.py 폴백)
  - ASCII 차트 포함
- `docs/reports/cost_sensitivity_v4_analysis.md` 신규 (스크립트 실행 시 자동 생성)

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
