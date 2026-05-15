# Deflated Sharpe Ratio + 통계적 유의성 검정 V2

생성: 2026-05-15 00:49:34  
기간: 2017-01-01 ~ 2024-12-31  
시장: KOSPI | 전략: V70M30 (Preset A)

## 기본 통계

| 항목 | 값 |
|------|---|
| 연율화 Sharpe (excess vs RF) | 0.453 |
| 월간 Sharpe | 0.079 |
| 연율화 변동성 | 16.33% |
| 월간 관측치 (T) | 92개월 |
| 월간 수익률 Skewness | -0.236 |
| 월간 수익률 Excess Kurtosis | 1.944 |
| N_trials (시행 횟수) | 20 (보수적) |
| SR_std 연율화 (추정) | 0.025 |
| E[max(SR_N)] 연율화 | 0.048 |
| KOSPI 연율화 Sharpe | 0.033 |

## PSR / DSR

| 지표 | 값 | 해석 |
|------|---|------|
| **DSR** | **0.729** | > 0.50: 양의 신호 |
| PSR (vs SR*=0) | 0.770 | > 0.95면 유의 |
| PSR (vs KOSPI) | 0.742 | > 0.95면 KOSPI 초과 유의 |

## t-statistic (비정규 보정)

| 항목 | 값 | 해석 |
|------|---|------|
| t-statistic | 0.744 | > 1.645면 p < 0.05 (단측) |
| p-value (단측) | 0.2283 | |

## Minimum Track Record Length (MinTRL)

| 벤치마크 | MinTRL (월) | MinTRL (년) | 해석 |
|----------|-----------|-----------|------|
| SR* = 0 | 450.3 | 37.5 | Sharpe > 0 증명에 필요한 최소 기간 |
| SR* = KOSPI (0.033) | 584.3 | 48.7 | KOSPI 초과 증명에 필요한 최소 기간 |

**현재 관측 기간**: 8년 (96개월)  
**MinTRL vs SR*=0**: 37.5년 → 현재 기간 대비 부족  

## 종합 판정

> **⚠️ 유의하지 않지만 양의 신호 — DSR 0.50~0.95**

## MinTRL 해석

MinTRL이 크다는 것은 전략이 나쁘다는 의미가 아님. 
Sharpe 0.245 수준의 전략은 정의상 통계적 증명에 긴 기간이 필요함.

- Sharpe 0.245 → MinTRL ≈ 37.5년 (SR*=0 기준)
- Sharpe 0.30 (개선 가정) → MinTRL ≈ 31.0년
- Sharpe 0.50 (강한 전략) → MinTRL ≈ 11.8년
- Sharpe 1.00 (헤지펀드급) → MinTRL ≈ 3.7년

## V3 연계: 팩터 구성 변경 시 DSR 개선 가능성

V3 IC/IR 분석 결과:

| 팩터 | IR | 해석 |
|------|---|------|
| Value 합산 | +0.572 | ★★★ 강한 예측력 |
| Momentum 합산 | -0.057 | ✗ 예측력 없음 |
| Quality 합산 | -0.221 | ✗ 예측력 없음 |
| Composite V70M30 | +0.533 | ★★★ |

**팩터 구성 개선 → DSR 개선 경로**:

1. **Momentum 음수 IC 영향**: 현재 V70M30에서 Momentum(IR=-0.057)이 Sharpe를 하방 압력. Value 단독(IR=+0.572) > Composite(IR=+0.533).

2. **Value 단독 시나리오 (Preset C, V=1.00)**: Momentum 제거 시 Sharpe가 0.453 → 0.30 수준으로 개선될 경우  
   MinTRL이 37.5년 → 31.0년으로 단축.  
   (현재 8년 관측치 기준 PSR 유의수준: 0.792 → 여전히 < 0.95지만 접근)

3. **근본 한계**: 8년 데이터는 Sharpe < 1.0인 전략을 통계적으로 '증명'하기에 구조적으로 부족.  
   DSR은 데이터 부족 경고지 전략 무효 판정이 아님.  
   실전 운용 성과(out-of-sample) 축적이 가장 강력한 검정.

## 참고 문헌

- Bailey, D.H. & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*. Journal of Portfolio Management.
- Opdyke, J.D. (2007). *Comparing Sharpe Ratios: So Where Are the p-values?* Journal of Asset Management.
