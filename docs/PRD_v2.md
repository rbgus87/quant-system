# PRD v2.0 — 한국 주식 멀티팩터 퀀트 자동매매 시스템 (전략 재설계)

> **버전**: 2.0 | **작성**: 2026-03-25 | **상태**: Phase 0 확정  
> **변경 사유**: 팩터 이중 가중 제거, 프리셋 통합, 백테스트 신뢰도 강화  
> **개발 환경**: Python 3.14 + Claude Code CLI + 키움 REST API

---

## 1. 변경 요약 (v1.1 → v2.0)

| 영역 | v1.1 (기존) | v2.0 (신규) | 변경 이유 |
|------|------------|------------|----------|
| Value 팩터 | PBR + **PER** + DIV | PBR + **PCR** + DIV | Quality의 EY(1/PER)와 이중 가중 제거 |
| Quality 팩터 | ROE + **EY** + 배당 + 부채비율 | **GP/A** + EY(1/PER) + F-Score | 수익성 지표 독립성 확보, F-Score 강화 |
| F-Score | 5점 간소화 (실효성 약함) | **DART 전기 비교 7점** 또는 제거 | 현 구현은 ROE>0 ≈ PER>0 중복 |
| 전략 프리셋 | 9개 (A~I) | **4개** (균형/딥밸류/모멘텀/방어) | 실질 차별화 안 되는 프리셋 제거 |
| 금액 프리셋 | 7단계 (100만~5억) | **4단계** (소/중/대/거) | 100만원 제거, 시장 강제변경 제거 |
| MDD 서킷브레이커 | 0.99 (사실상 비활성화) | **전략별 명시적 설정** | 실전 리스크 관리 필수 |
| vol_target | 0.99 (사실상 비활성화) | **전략별 명시적 설정** | 변동성 타겟팅 실질 활성화 |
| 백테스트 | 단일 기간 | **Walk-Forward 추가** | Out-of-Sample 검증 필수 |
| Reporting Lag | 미처리 | **결산월+3개월 래그 적용** | Look-Ahead Bias 차단 |
| 모멘텀 유효 데이터 | counts >= 10 | **counts >= lookback × 0.7** | 데이터 구멍 종목 오진입 방지 |

---

## 2. 전략 명세 (Strategy Spec v2.0)

### 2-1. 유니버스 (변경 없음 — 전략이 결정)

| 항목 | 기본값 | 비고 |
|------|-------|------|
| 대상 시장 | KOSPI | 전략별 KOSDAQ/ALL 선택 가능 |
| 시가총액 하한 | 하위 10% 제외 | 전략별 조정 가능 (5~20%) |
| 제외 섹터 | 금융주 전체 | 은행, 증권, 보험, 기타금융, 다각화된금융 |
| 상장 요건 | 1년 이상 | 가격 이력 확보 |
| 유동성 | 20일 평균 거래대금 ≥ 2억원 | 금액 프리셋이 상향 가능, 하향 불가 |

### 2-2. 팩터 정의 (v2.0 — 핵심 변경)

#### Value 팩터 (자산·현금흐름·배당 — PER 제거)

| 지표 | 가중치 | 계산 | 방향 |
|------|-------|------|------|
| PBR | 50% | 주가 / 주당순자산 → 역수 | 낮을수록 우수 |
| **PCR** | 30% | 주가 / 주당영업현금흐름 → 역수 | 낮을수록 우수 (신규) |
| DIV | 20% | 배당수익률 | 높을수록 우수 |

> **PER → PCR 변경 이유**: Quality 팩터의 Earnings Yield(1/PER)와 동일 데이터를 사용하던  
> 이중 가중 문제 해결. PCR은 이익 조작에 강건하고 한국 시장에서 별도 설명력 보유.  
> **PCR 데이터 소스**: DART OpenAPI 재무제표 → 영업활동현금흐름 / 발행주식수  
> **폴백**: PCR 데이터 없는 종목은 PBR+DIV 2팩터로 가중치 재분배 (기존 NaN-aware 로직)

#### Momentum 팩터 (변경 최소)

| 지표 | 가중치 | 계산 |
|------|-------|------|
| 12M 수익률 | 60% | t-1M ~ t-12M (최근 1개월 제외) |
| 6M 수익률 | 30% | t-1M ~ t-6M |
| 3M 수익률 | 10% | (선택) t-1M ~ t-3M |

> **듀얼 모멘텀**: 12M 수익률 > 무위험 수익률(동적 참조) 필터 지원  
> **유효 데이터 기준 강화**: `counts >= lookback_trading_days × 0.7` (기존 10 → 약 170일)

#### Quality 팩터 (전면 재설계)

| 지표 | 가중치 | 계산 | 방향 |
|------|-------|------|------|
| **GP/A** | 40% | 매출총이익 / 총자산 | 높을수록 우수 (신규) |
| EY | 30% | 1 / PER (Earnings Yield) | 높을수록 우수 (Value에서 이동) |
| F-Score | 30% | 7점 피오트로스키 (DART 전기 비교) | 높을수록 우수 (강화) |

> **GP/A (Gross Profitability)**: Novy-Marx (2013) 논문 기반.  
> ROE보다 이익 조작에 강건하고, Value 팩터와 음의 상관관계를 보여 분산 효과 제공.  
> **GP/A 데이터 소스**: DART OpenAPI → 매출총이익(손익계산서) / 총자산(재무상태표)  
> **F-Score 강화안** (DART 전기 데이터 활용 가능 시):
>   1. ROA > 0 (+1)
>   2. 영업현금흐름 > 0 (+1)  
>   3. ROA 전기 대비 증가 (+1)
>   4. 영업현금흐름 > 당기순이익 (+1, 발생주의 품질)
>   5. 부채비율 전기 대비 감소 (+1)
>   6. 유동비율 전기 대비 증가 (+1)
>   7. 매출총이익률 전기 대비 증가 (+1)
>
> **F-Score 폴백** (DART 전기 데이터 미확보 시):
>   F-Score를 제거하고 GP/A 50% + EY 50%로 재분배.
>   빈약한 F-Score를 넣는 것보다 제거하는 게 나음.

#### 복합 스코어 (Composite)

```
composite_score = V × value_score + M × momentum_score + Q × quality_score
```

| 전략 | V | M | Q |
|------|---|---|---|
| 균형 (A) | 0.35 | 0.40 | 0.25 |
| 딥밸류 (B) | 0.60 | 0.00 | 0.40 |
| 모멘텀 (C) | 0.10 | 0.70 | 0.20 |
| 방어 (D) | 0.35 | 0.20 | 0.45 |

### 2-3. Reporting Lag (신규 — Look-Ahead Bias 차단)

| 결산 유형 | 발표 지연 | 사용 가능 시점 |
|----------|----------|--------------|
| 연간 보고서 (12월 결산) | +90일 (3개월) | 4월 리밸런싱부터 |
| 분기 보고서 | +45일 | 발표 +1영업일부터 |
| 반기 보고서 | +60일 | 발표 +1영업일부터 |

> **구현 방식**: `screener.py`에서 재무 데이터 조회 시  
> `effective_date = report_date + reporting_lag` 이후에만 해당 데이터 사용.  
> 12월 결산 기업의 연간 실적은 3월 말까지 발표 → 4월 리밸런싱부터 반영.

### 2-4. 거래 비용 (변경 없음)

| 항목 | 적용값 | 비고 |
|------|--------|------|
| 수수료 | 0.015% | 매수/매도 공통 (키움 HTS 기준) |
| 증권거래세 | 0.18% | 매도 시만 적용 |
| 슬리피지 | 0.10% | 대형주 기준 (금액 프리셋이 상향 가능) |

---

## 3. 프리셋 시스템 v2.0

### 3-1. 설계 원칙

```
전략 프리셋 → "어떤 종목을 고르고, 어떻게 방어할 것인가" (팩터, 필터, 리스크 관리)
금액 프리셋 → "얼마로, 몇 종목을, 어떤 유동성 기준으로" (종목 수, 유동성, 슬리피지)

※ 금액 프리셋은 전략 프리셋의 핵심 설정(market, min_market_cap_percentile)을 덮어쓰지 않는다.
※ 금액 프리셋이 변경 가능한 것: n_stocks, initial_cash, min_avg_trading_value, slippage, max_position_pct
```

### 3-2. 전략 프리셋 (4개)

#### A: 밸류+모멘텀 균형 (기준선)

학술적으로 가장 안정적인 3팩터 조합. 대부분의 시장 국면에서 양호한 성과.

```yaml
A:
  factor_weights: { value: 0.35, momentum: 0.40, quality: 0.25 }
  value_weights: { pbr: 0.50, pcr: 0.30, div: 0.20 }
  universe: { market: "KOSPI", min_market_cap_percentile: 10.0 }
  momentum: { absolute_momentum_enabled: true }
  quality: { fscore_enabled: true, min_fscore: 4 }
  volatility: { max_percentile: 80.0 }
  market_regime: { enabled: true, partial_ratio: 0.6, defensive_ratio: 0.4 }
  trading:
    max_turnover_pct: 0.50
    trailing_stop_pct: 0.25
    max_drawdown_pct: 0.25    # 활성화: -25% 서킷브레이커
    vol_target: 0.15           # 활성화: 연환산 15% 타겟
```

#### B: 딥밸류 (하락장 매수)

모멘텀을 완전 배제. 저평가 종목을 장기 보유하며 평균 회귀를 노림.
하락장에서 절대 모멘텀이 매수를 막지 않도록 비활성화.

```yaml
B:
  factor_weights: { value: 0.60, momentum: 0.00, quality: 0.40 }
  value_weights: { pbr: 0.50, pcr: 0.30, div: 0.20 }
  universe: { market: "KOSPI", min_market_cap_percentile: 10.0 }
  momentum: { absolute_momentum_enabled: false }
  quality: { fscore_enabled: true, min_fscore: 4 }
  volatility: { max_percentile: 90.0 }           # 완화: 저PBR 소형주 살리기
  market_regime: { enabled: true, partial_ratio: 0.8, defensive_ratio: 0.6 }
  trading:
    max_turnover_pct: 0.30                        # 낮은 교체율 (장기 보유)
    trailing_stop_pct: 0.30                       # 넓은 스톱 (밸류트랩 허용)
    max_drawdown_pct: 0.30                        # 밸류는 MDD 내성 높게
    vol_target: 0.18                              # 완화된 타겟
```

#### C: 모멘텀 추세추종 (강세장 극대화)

강세장 수익 극대화. 약세 전환 시 시장 레짐 필터가 현금 방어.
유일하게 KOSPI+KOSDAQ 전체 유니버스 사용 (모멘텀 종목 풀 확대).

```yaml
C:
  factor_weights: { value: 0.10, momentum: 0.70, quality: 0.20 }
  value_weights: { pbr: 0.50, pcr: 0.30, div: 0.20 }
  universe: { market: "ALL", min_market_cap_percentile: 15.0 }
  momentum: { absolute_momentum_enabled: true }
  quality: { fscore_enabled: true, min_fscore: 3 }
  volatility: { max_percentile: 70.0 }           # 강화: 고변동성 30% 제거
  market_regime: { enabled: true, partial_ratio: 0.5, defensive_ratio: 0.2 }
  trading:
    max_turnover_pct: 0.60                        # 빠른 교체 허용
    trailing_stop_pct: 0.20                       # 적정 스톱 (0.15는 너무 타이트)
    max_drawdown_pct: 0.20                        # 타이트한 서킷브레이커
    vol_target: 0.15                              # 표준 타겟
```

#### D: 방어형 (최소변동성 + 고배당)

MDD 최소화와 Sharpe 극대화에 집중. 배당 수익 + 저변동성 종목.
보합장/약세장에서 안정적 수익.

```yaml
D:
  factor_weights: { value: 0.35, momentum: 0.20, quality: 0.45 }
  value_weights: { pbr: 0.30, pcr: 0.20, div: 0.50 } # 배당 비중 극대화
  universe: { market: "KOSPI", min_market_cap_percentile: 10.0 }
  momentum: { absolute_momentum_enabled: true }
  quality: { fscore_enabled: true, min_fscore: 5 }    # 재무 건전성 최강 필터
  volatility: { max_percentile: 50.0 }                # 상위 50% 변동성 제거 (핵심)
  market_regime: { enabled: true, partial_ratio: 0.6, defensive_ratio: 0.3 }
  trading:
    max_turnover_pct: 0.25                             # 최소 교체 (장기 보유)
    trailing_stop_pct: 0.20
    max_drawdown_pct: 0.15                             # 가장 타이트한 서킷브레이커
    vol_target: 0.10                                   # 가장 낮은 변동성 타겟
```

### 3-3. 금액 프리셋 (4단계)

| 프리셋 | 금액 범위 | 종목 수 | 유동성 하한 | 슬리피지 | 최대 단일 비중 |
|--------|----------|---------|-----------|---------|-------------|
| 소액 | ~500만원 | 10 | 1억 | 0.10% | 15% |
| 중액 | 1000~3000만원 | 20 | 2억 | 0.10% | 7% |
| 대액 | 5000만~1억원 | 25 | 5억 | 0.15% | 5% |
| 거액 | 3억원~ | 30 | 10억 | 0.20% | 3.5% |

> **금액 프리셋이 변경하지 않는 것**: market, min_market_cap_percentile, factor_weights,  
> 모든 리스크 관리 파라미터 (trailing_stop, max_drawdown, vol_target, market_regime)  
> 이들은 전략 프리셋이 전적으로 결정한다.

### 3-4. 프리셋 충돌 방지 규칙

```python
# settings.py에서 검증
STRATEGY_ONLY_KEYS = {
    "factor_weights", "value_weights", "momentum", "quality",
    "volatility", "market_regime",
    "trading.max_drawdown_pct", "trading.vol_target",
    "trading.trailing_stop_pct", "trading.max_turnover_pct",
}
SIZING_ONLY_KEYS = {
    "portfolio.n_stocks", "portfolio.initial_cash",
    "trading.slippage", "trading.max_position_pct",
}
# 금액 프리셋이 STRATEGY_ONLY_KEYS를 포함하면 WARNING + 무시
```

---

## 4. 백테스트 v2.0

### 4-1. 기본 백테스트 (변경 사항)

- **T일 신호 → T+1 시가 체결** (변경 없음)
- **Reporting Lag 적용**: 재무 데이터는 발표 시점+래그 이후에만 사용
- **모멘텀 유효 데이터 기준**: counts >= lookback_trading_days × 0.7
- **생존자 편향**: KRX API 실패 시 직전 성공 유니버스 사용 (빈 DataFrame 반환 금지)

### 4-2. Walk-Forward 백테스트 (신규)

```
[2010-2014 학습] → [2015-2016 검증] → 성과 기록
[2012-2016 학습] → [2017-2018 검증] → 성과 기록
[2014-2018 학습] → [2019-2020 검증] → 성과 기록
[2016-2020 학습] → [2021-2022 검증] → 성과 기록
[2018-2022 학습] → [2023-2024 검증] → 성과 기록
```

- 학습 기간: 4~5년, 검증 기간: 2년
- 학습 기간에서 파라미터 최적화 → 검증 기간에서 성과 측정
- 5개 윈도우의 검증 성과를 이어붙인 것이 "실전 기대 수익률"

### 4-3. 파라미터 튜닝 프로토콜

```
카테고리 1 (학술 고정): 모멘텀 룩백 12M, Winsorize 1%, 리밸런싱 월 1회
카테고리 2 (Grid Search): 팩터 가중치, 종목 수, 시총 하한, 변동성 필터
카테고리 3 (리스크 허용도): MDD 서킷브레이커, 트레일링 스톱, vol_target

튜닝 순서: 카테고리 1 고정 → 카테고리 2 탐색 → 인접 안정성 검증
           → Walk-Forward 확인 → 카테고리 3 개인 설정 → 프리셋 확정
```

### 4-4. 인접 안정성 검증 (Robustness Check)

최적 파라미터 P*에 대해:
- P* ± 10% 범위에서 Sharpe Ratio 변동이 20% 이내여야 함
- 변동이 20% 초과하면 해당 파라미터는 노이즈에 과적합된 것

---

## 5. 성과 목표 (KPI v2.0)

| 지표 | 목표 | 양호 | 최소 통과 |
|------|------|------|----------|
| CAGR (Walk-Forward OOS) | 15%+ | 12%+ | 8%+ |
| MDD | -20% 이내 | -25% 이내 | -30% 이내 |
| Sharpe Ratio | 1.0+ | 0.8+ | 0.6+ |
| Sortino Ratio | 1.5+ | 1.2+ | 0.8+ |
| Calmar Ratio | 0.7+ | 0.5+ | 0.3+ |
| KOSPI 대비 초과수익 | 연 5%+ | 연 3%+ | 연 1%+ |
| 연간 턴오버 | 300% 이하 | 400% 이하 | 500% 이하 |
| Walk-Forward 양의 수익 비율 | 5/5 윈도우 | 4/5 | 3/5 |

> **"최소 통과" 미달 시**: 해당 전략 프리셋은 실전 투입 불가.  
> 파라미터 재탐색 또는 전략 자체를 재검토.

---

## 6. 수술 범위 요약

### 유지 (인프라 계층)
- `data/collector.py` — multi-tier 폴백 구조 그대로
- `data/storage.py` — ORM 모델 그대로 (PCR 컬럼 추가만)
- `data/dart_client.py` — GP/A, 현금흐름 데이터 메서드 추가
- `trading/kiwoom_api.py` — 그대로
- `trading/order.py` — 그대로
- `config/calendar.py` — 그대로
- `notify/telegram.py` — 그대로
- `scheduler/main.py` — 프리셋 수 변경에 따른 미세 조정 + vol_target 중복 제거
- `gui/` — 현행 유지 (PyQt 기반 GUI, 별도 수정 불필요)

### 전면 재설계 (전략 계층)
- `factors/value.py` — PER → PCR 교체
- `factors/quality.py` — ROE → GP/A, F-Score 강화 또는 제거
- `factors/momentum.py` — 유효 데이터 기준 강화
- `factors/composite.py` — 변경 최소 (가중치만 새 프리셋 반영)
- `config/config.yaml` — 9+7 프리셋 → 4+4 프리셋
- `strategy/screener.py` — Reporting Lag 처리 추가

### 부분 수정 (엔진 계층)
- `config/settings.py` — 프리셋 충돌 감지, null 비활성화 지원, PCR 관련 설정
- `backtest/engine.py` — Walk-Forward 교체, 생존자 편향 폴백, 배당 추정 제거
- `backtest/metrics.py` — RF_ANNUAL 상수 → 동적 참조
- `strategy/market_regime.py` — risk_free_rate 동적 참조, vol_target 공통 함수 추출
- `strategy/screener.py` — 팩터 캐시 메모리 제한 추가
- `data/collector.py` — PCR 계산용 현금흐름 데이터 수집 메서드 추가
- `data/processor.py` — PCR 전처리 블록 추가

### 삭제/비활성화
- `backtest/engine.py`의 `_estimate_dividend_income()` — `@deprecated` 처리
  - 사유: 한국 시장은 연 1회 배당 집중. 월별 균등 배분은 수익률 왜곡.
  - 백테스트 수익률에 배당은 미포함 (보수적 추정).
  - 실전 운용에서는 키움 API 잔고 조회 시 배당금 자동 반영.

---

## 7. 위험 요소

1. **PCR 데이터 가용성**: DART에서 영업활동현금흐름을 안정적으로 가져올 수 있는지 검증 필요.
   가져올 수 없으면 PSR(주가매출비율)로 대체 가능.
2. **GP/A 데이터**: DART 손익계산서에서 매출총이익이 누락된 기업이 있을 수 있음.
   매출액 - 매출원가로 직접 계산하는 폴백 필요.
3. **Walk-Forward 구현 복잡도**: 기존 엔진이 단일 기간 전제로 설계됨.
   학습/검증 윈도우 분리 로직 추가 시 엔진 인터페이스 변경 가능.
4. **F-Score 강화 시 DART 호출 증가**: 전기 재무제표까지 필요하면 API 호출량 2배.
   캐시 전략 필요.
