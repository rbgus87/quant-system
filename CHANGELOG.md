# Changelog

본 프로젝트의 주요 변경 사항. [Keep a Changelog](https://keepachangelog.com/) 형식.

---

## [Unreleased]

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
