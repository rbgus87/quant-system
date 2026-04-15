# 대안 방어 장치 비교 리포트

**작성일**: 2026-04-15  
**배경**: strict_reporting_lag=True 실험에서 CAGR -12.18%p 부작용 확인 → 원복. 005620 유형 재발 방지를 위한 3가지 대안 방어 장치 효과 측정.

## 종합 비교

| 시나리오 | CAGR | ΔCAGR | MDD | Sharpe | 005620 회피 | 폐지 노출 | Baseline 대비 겹침 |
|----------|------|-------|-----|--------|-------------|-----------|---------------------|
| Baseline | +18.76% | +0.00%p | -30.92% | 0.542 | ❌ | 1건 | 100.0% |
| +방어1_EPS반전 | +11.67% | -7.08%p | -45.40% | 0.379 | ✅ | 0건 | 84.5% |
| +방어2_거래정지이력 | +18.70% | -0.06%p | -30.92% | 0.540 | ❌ | 1건 | 99.4% |
| +방어3_min_fscore_5 | +6.03% | -12.73%p | -44.24% | 0.244 | ✅ | 0건 | 45.2% |
| +1+2+3_통합 | +5.37% | -13.38%p | -45.22% | 0.216 | ✅ | 0건 | 39.1% |

## 상세 지표

### Baseline

- 설정: `{'strict_reporting_lag': False, 'eps_flip_filter_enabled': False, 'halt_history_filter_enabled': False, 'min_fscore': 4}`
- CAGR: **+18.76%**
- MDD: **-30.92%**
- Sharpe: **0.542**
- Sortino: **1.313**
- Calmar: **0.607**
- 평균 분기 회전율: 56.5%, 평균 선정 종목: 20.0개
- 005620 (2017-06-30) 선정: **선정됨 ❌**
- 전체 기간 failure 폐지 노출: **1건**
- Baseline 대비 평균 종목 겹침: 100.0% (총 누락 0건)

### +방어1_EPS반전

- 설정: `{'strict_reporting_lag': False, 'eps_flip_filter_enabled': True, 'halt_history_filter_enabled': False, 'min_fscore': 4}`
- CAGR: **+11.67%**
- MDD: **-45.40%**
- Sharpe: **0.379**
- Sortino: **0.874**
- Calmar: **0.257**
- 평균 분기 회전율: 54.7%, 평균 선정 종목: 20.0개
- 005620 (2017-06-30) 선정: **회피 ✅**
- 전체 기간 failure 폐지 노출: **0건**
- Baseline 대비 평균 종목 겹침: 84.5% (총 누락 99건)

### +방어2_거래정지이력

- 설정: `{'strict_reporting_lag': False, 'eps_flip_filter_enabled': False, 'halt_history_filter_enabled': True, 'min_fscore': 4}`
- CAGR: **+18.70%**
- MDD: **-30.92%**
- Sharpe: **0.540**
- Sortino: **1.315**
- Calmar: **0.605**
- 평균 분기 회전율: 56.0%, 평균 선정 종목: 20.0개
- 005620 (2017-06-30) 선정: **선정됨 ❌**
- 전체 기간 failure 폐지 노출: **1건**
- Baseline 대비 평균 종목 겹침: 99.4% (총 누락 4건)

### +방어3_min_fscore_5

- 설정: `{'strict_reporting_lag': False, 'eps_flip_filter_enabled': False, 'halt_history_filter_enabled': False, 'min_fscore': 5}`
- CAGR: **+6.03%**
- MDD: **-44.24%**
- Sharpe: **0.244**
- Sortino: **0.390**
- Calmar: **0.136**
- 평균 분기 회전율: 61.1%, 평균 선정 종목: 20.0개
- 005620 (2017-06-30) 선정: **회피 ✅**
- 전체 기간 failure 폐지 노출: **0건**
- Baseline 대비 평균 종목 겹침: 45.2% (총 누락 351건)

### +1+2+3_통합

- 설정: `{'strict_reporting_lag': False, 'eps_flip_filter_enabled': True, 'halt_history_filter_enabled': True, 'min_fscore': 5}`
- CAGR: **+5.37%**
- MDD: **-45.22%**
- Sharpe: **0.216**
- Sortino: **0.339**
- Calmar: **0.119**
- 평균 분기 회전율: 63.1%, 평균 선정 종목: 20.0개
- 005620 (2017-06-30) 선정: **회피 ✅**
- 전체 기간 failure 폐지 노출: **0건**
- Baseline 대비 평균 종목 겹침: 39.1% (총 누락 390건)

## 결론 및 권고

기준: CAGR 손실 -1%p 이내 + 005620 회피 + 폐지 노출 감소

⚠️ **CAGR -1%p 이내 조건을 만족하는 방어 장치 없음**
- 최선은 `+방어2_거래정지이력` (ΔCAGR -0.06%p, 폐지 노출 1건)
- 적용 기준을 -2%p로 완화하거나, 방어 장치 없이 `risk_guard` 일일 알림으로만 005620 유형 대응 고려

## 한계점

- 간이 백테스트 (시장 레짐/변동성 타겟팅 미반영)
- 거래비용 고정 0.5%/교체율
- 폐지 failure 종목은 -100% 가정
