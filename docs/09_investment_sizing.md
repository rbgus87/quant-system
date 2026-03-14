# 투자금별 최적 설정 가이드

> 작성일: 2026-03-13
> 대상: `config/config.yaml` 파라미터 조정 가이드

## 1. 개요

이 문서는 투자금 규모에 따라 `config.yaml`의 설정값을 어떻게 조정해야 하는지 안내합니다.
투자금이 달라지면 **종목 수, 유동성 필터, 슬리피지, 시가총액 필터** 등이 함께 변경되어야
전략의 효과를 극대화하고 시장 충격(Market Impact)을 최소화할 수 있습니다.

### 핵심 원칙

```
투자금 ↑ → 종목 수 ↑, 유동성 필터 ↑, 슬리피지 ↑, 시총 필터 ↑
투자금 ↓ → 종목 수 ↓ (최소 5), 유동성 필터 ↓, 집중투자 성격 강화
```

### 핵심 제약 조건

| 제약 | 기준 |
|------|------|
| 종목당 최소 투자금 | 약 50만원 이상 (호가 단위 반올림 오차 최소화) |
| 종목당 적정 투자금 | 소액: 100만~500만원, 중대형: 1,000만~5,000만원 |
| 분산 효과 한계 | 15종목부터 체감, 30종목 이상은 한계 효용 급감 |
| 시장 충격 임계점 | 종목당 투자금 > 일평균 거래대금의 1% 시 슬리피지 급증 |

---

## 2. 투자금별 최적 파라미터 요약

| 설정 | 100만원 | 500만원 | 1,000만원 | 3,000만원 | 5,000만원 | 1억 | 5억 |
|------|---------|---------|-----------|-----------|-----------|-----|-----|
| **n_stocks** | 5 | 7 | 10 | 15 | 20 | 25 | 30 |
| 종목당 투자금 | 20만원 | 71만원 | 100만원 | 200만원 | 250만원 | 400만원 | 1,667만원 |
| **market** | KOSPI | KOSPI | KOSPI | KOSPI | KOSPI | ALL | KOSPI |
| **min_market_cap_percentile** | 10 | 10 | 10 | 10 | 10 | 10 | 20 |
| **min_avg_trading_value** | 1억 | 1억 | 2억 | 2억 | 3억 | 5억 | 10억 |
| **slippage** | 0.1% | 0.1% | 0.1% | 0.1% | 0.1% | 0.15% | 0.2% |
| **max_position_pct** | 20% | 15% | 10% | 7% | 5% | 4% | 3.5% |
| **max_turnover_pct** | 50% | 50% | 50% | 50% | 50% | 40% | 30% |
| **weight_method** | equal | equal | equal | equal | equal | equal | equal |

---

## 3. 투자금 구간별 상세 설명

### 3.1 100만원 (초소액)

```yaml
portfolio:
  n_stocks: 5
  initial_cash: 1000000
  weight_method: "equal"
trading:
  max_position_pct: 0.20
  slippage: 0.001
  max_turnover_pct: 0.50
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 100000000  # 1억
```

**특성**:
- 종목당 20만원으로 **분산 효과가 매우 제한적**
- 호가 단위 반올림 오차가 3~5% 발생 가능 (저가주 위주 투자 시 완화)
- 5종목이 사실상 하한선 — 그 이하는 팩터 전략의 의미가 없음

**현실적 한계**:
- 퀀트 전략보다 1~2종목 집중투자가 더 효율적인 금액대
- 팩터 프리미엄보다 거래비용 비중이 상대적으로 큼

---

### 3.2 500만원 (소액)

```yaml
portfolio:
  n_stocks: 7
  initial_cash: 5000000
  weight_method: "equal"
trading:
  max_position_pct: 0.15
  slippage: 0.001
  max_turnover_pct: 0.50
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 100000000  # 1억
```

**특성**:
- 종목당 약 71만원 — 최소한의 분산 가능
- 호가 단위 오차 1~2% 수준으로 관리 가능
- 멀티팩터 전략이 작동하기 시작하는 최소 금액대

---

### 3.3 1,000만원 (적정 소액, 현재 기본값)

```yaml
portfolio:
  n_stocks: 10
  initial_cash: 10000000
  weight_method: "equal"
trading:
  max_position_pct: 0.10
  slippage: 0.001
  max_turnover_pct: 0.50
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 200000000  # 2억
```

**특성**:
- 종목당 100만원 — 호가 단위 오차 무시 가능
- 10종목 분산은 개인 투자자 기준 합리적
- **현재 시스템 기본 설정이 이미 이 구간에 최적화**되어 있음

---

### 3.4 3,000만원 (중소형)

```yaml
portfolio:
  n_stocks: 15
  initial_cash: 30000000
  weight_method: "equal"
trading:
  max_position_pct: 0.07
  slippage: 0.001
  max_turnover_pct: 0.50
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 200000000  # 2억
```

**특성**:
- 종목당 200만원 — 분산 효과 본격화
- 15종목부터 비체계적 위험(개별 종목 리스크) 80% 이상 제거
- 팩터 프리미엄 포착에 충분한 종목 수

---

### 3.5 5,000만원 (중형)

```yaml
portfolio:
  n_stocks: 20
  initial_cash: 50000000
  weight_method: "equal"
trading:
  max_position_pct: 0.05
  slippage: 0.001
  max_turnover_pct: 0.50
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 300000000  # 3억
```

**특성**:
- 종목당 250만원 — 최적 균형점
- 20종목으로 분산 효과 극대화 구간 진입
- 거래대금 필터를 3억으로 올려 유동성 확보 필요

---

### 3.6 1억 (대형)

```yaml
portfolio:
  n_stocks: 25
  initial_cash: 100000000
  weight_method: "equal"
trading:
  max_position_pct: 0.04
  slippage: 0.0015           # 0.15% (상향)
  max_turnover_pct: 0.40     # 교체율 제한 강화
universe:
  market: "ALL"              # KOSPI + KOSDAQ 유니버스 확장 가능
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 500000000  # 5억
```

**특성**:
- 종목당 400만원 — 시장 충격 미미하지만 관리 시작 필요
- 유동성 필터 강화 필수 (5억 이상)
- 슬리피지 보수적 상향 (0.15%)
- KOSDAQ 포함 시 유니버스 확대로 팩터 분산 향상 가능

---

### 3.7 5억 (초대형, 개인 기준)

```yaml
portfolio:
  n_stocks: 30
  initial_cash: 500000000
  weight_method: "equal"
trading:
  max_position_pct: 0.035
  slippage: 0.002            # 0.2% (상향)
  max_turnover_pct: 0.30     # 교체율 제한 강화
universe:
  market: "KOSPI"            # KOSPI만 (유동성 확보)
  min_market_cap_percentile: 20.0  # 시총 하위 20% 제외 (상향)
  min_avg_trading_value: 1000000000  # 10억
market_regime:
  partial_ratio: 0.7
  defensive_ratio: 0.5
```

**특성**:
- 종목당 약 1,667만원 — 시장 충격 관리가 핵심
- 30종목 완전 분산으로 비체계적 위험 거의 제거
- 소형주 유동성 부족 → 중대형주 집중 (시총 하위 20% 제외)
- 월간 교체율을 30%로 제한하여 대량 교체 시 시장 충격 방지

---

## 4. 5억 투자 시 주요 변경 포인트

현재 기본 설정(1,000만원)과 5억 투자 시 권장 설정을 비교합니다.

| 항목 | 현재 (1,000만원) | 5억 권장 | 변경 이유 |
|------|-----------------|---------|----------|
| **n_stocks** | 10 | **30** | 분산 극대화 + 시장 충격 분산 |
| **min_market_cap_percentile** | 10% | **20%** | 소형주 유동성 부족 → 중대형주 집중 |
| **min_avg_trading_value** | 2억 | **10억** | 종목당 1,667만원 체결 시 충격 최소화 |
| **slippage** | 0.1% | **0.2%** | 대량 주문 시 호가 밀림 반영 |
| **max_position_pct** | 10% | **3.5%** | 30종목 동일가중 (1/30 = 3.3%) |
| **max_turnover_pct** | 50% | **30%** | 대량 교체 시 시장 충격 방지 |
| **market** | KOSPI | **KOSPI** | 유동성 확보 (KOSDAQ 소형주 제외) |

### 5억 투자 시 추가 고려사항

1. **분할 매매**: 종목당 1,667만원은 시가 일괄 체결이 가능하지만,
   소형주는 일 거래대금의 1%를 초과할 수 있음 → 유동성 필터 10억이 핵심
2. **리밸런싱 비용**: 30종목 월간 교체 시 거래비용 누적 →
   `max_turnover_pct: 0.30`으로 제한하여 비용 관리
3. **세금 영향**: 매도 거래세 0.20%가 대규모 매매 시 유의미 →
   불필요한 종목 교체 최소화
4. **시장 레짐**: 5억 규모에서는 하락장 방어가 절대 수익 기준으로 중요 →
   `defensive_ratio: 0.5` 유지 적절

---

## 5. 투자금 구간별 전략 성격 요약

| 구간 | 최적 종목 수 | 핵심 전략 성격 |
|------|-------------|---------------|
| 100만~500만원 | 5~7 | 집중 투자, 팩터보다 종목 선택이 중요 |
| 1,000만~3,000만원 | 10~15 | **팩터 전략 본격 작동**, 비용 효율 최적 |
| 5,000만~1억 | 20~25 | 분산 최적화, 유동성 관리 시작 |
| 5억 | 30 | 완전 분산, 시장 충격 관리 필수 |

---

## 6. 설정 변경 방법

`config/config.yaml` 파일에서 해당 투자금에 맞는 값을 수정합니다.

```yaml
# 예시: 5,000만원 투자 설정
portfolio:
  n_stocks: 20
  initial_cash: 50000000
  weight_method: "equal"

trading:
  max_position_pct: 0.05
  slippage: 0.001

universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  min_avg_trading_value: 300000000
```

또는 백테스트 CLI에서 `--cash` 플래그로 초기 자금을 지정할 수 있습니다:

```bash
python -m backtest.engine --cash 50000000
```

> **주의**: `--cash`로 투자금을 변경하더라도 `n_stocks`, `min_avg_trading_value` 등은
> 별도로 `config.yaml`에서 조정해야 합니다. 투자금만 바꾸면 종목당 투자금이 달라져
> 시장 충격이나 분산 효과가 최적에서 벗어날 수 있습니다.

---

## 7. 팩터 가중치 (투자금 무관, 공통)

팩터 가중치는 투자금 규모와 무관하게 동일하게 유지합니다.

```yaml
factor_weights:
  value: 0.40       # 밸류 (PBR 50%, PER 30%, 배당 20%)
  momentum: 0.40    # 모멘텀 (12M 60%, 6M 30%, 3M 10%)
  quality: 0.20     # 퀄리티 (ROE 40%, 1/PER 30%, 배당지급 30%)
```

팩터 가중치 변경이 필요한 경우는 전략 자체를 수정하는 것이므로,
별도의 백테스트 검증 후 조정해야 합니다.

---

## 8. 리스크 관리 설정 (투자금별 조정 불필요)

아래 설정은 비율 기반이므로 투자금 규모와 무관하게 동일하게 적용됩니다.

| 설정 | 값 | 설명 |
|------|-----|------|
| `max_drawdown_pct` | 0.99 | MDD 서킷브레이커 (현재 비활성화) |
| `trailing_stop_pct` | 0.20 | 종목별 트레일링 스톱 (-20%) |
| `vol_target` | 0.99 | 변동성 타겟팅 (현재 비활성화) |
| `vol_lookback_days` | 60 | 변동성 계산 기간 |
| `commission_rate` | 0.00015 | 수수료 (0.015%) |
| `tax_rate` | 0.0020 | 거래세 (0.20%, 매도만) |

> **참고**: `max_drawdown_pct`와 `vol_target`은 현재 0.99로 사실상 비활성화 상태입니다.
> 밸류 전략 특성상 하락장에서 리밸런싱이 유리하기 때문입니다.
> 대규모 자금(5억 이상)에서 보수적으로 운용하려면 각각 0.30, 0.12~0.18로 조정을 검토하세요.
