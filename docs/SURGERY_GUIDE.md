# SURGERY_GUIDE.md — v2.0 전략 재설계 수술 가이드

> **목적**: 기존 코드베이스의 인프라를 유지하면서 전략 계층만 재설계  
> **예상 소요**: 2~4주 (Claude Code CLI 활용)  
> **전제**: `docs/PRD_v2.md`와 루트 `CLAUDE.md` 확정 완료

---

## 수술 원칙

1. **인프라 건드리지 않기**: data/collector, storage, trading/, notify/, scheduler/는 최소 변경
2. **한 번에 하나씩**: Phase별로 커밋하고 테스트 통과 확인 후 다음 Phase 진행
3. **기존 테스트 깨뜨리지 않기**: 수술 대상 모듈의 테스트만 업데이트, 나머지는 유지
4. **백테스트는 마지막에**: 팩터 교체 → 스크리너 수정 → 프리셋 정리 → 백테스트 순서

---

## Phase 1: 팩터 재구축 (3~5일)

### 1-1. Value 팩터 — PER → PCR 교체

**파일**: `factors/value.py`

**변경 사항**:
```python
# 기존: PBR(0.5) + PER(0.3) + DIV(0.2)
# 신규: PBR(0.5) + PCR(0.3) + DIV(0.2)

# PCR = 주가 / 주당영업현금흐름
# PCR이 낮을수록 저평가 → 역수 변환 후 순위 스코어
# PCR <= 0 (영업현금흐름 마이너스) 종목은 제외
```

**config/settings.py 변경**:
```python
@dataclass
class ValueWeights:
    pbr: float = 0.50
    pcr: float = 0.30   # per → pcr 변경
    div: float = 0.20
```

**데이터 확보 (data/dart_client.py 추가)**:
```python
def get_operating_cashflow(self, corp_code: str, year: int) -> Optional[float]:
    """DART 재무제표에서 영업활동현금흐름 조회"""
    # 현금흐름표 → 영업활동으로인한현금흐름 항목
    pass

def calc_pcr(self, ticker: str, date: str) -> Optional[float]:
    """PCR = 현재가 / (영업활동현금흐름 / 발행주식수)"""
    pass
```

**data/storage.py 변경**:
```python
# Fundamental 테이블에 pcr 컬럼 추가
class Fundamental(Base):
    # ... 기존 컬럼 ...
    pcr = Column(Float)  # 신규
```

**폴백**: PCR 데이터가 없는 종목은 PBR+DIV 2팩터로 가중치 재분배 (기존 `weighted_average_nan_safe` 활용)

**PCR 확보 불가 시 대안**: PSR(주가매출비율) = 시가총액 / 매출액. DART에서 매출액은 거의 모든 기업이 공시.

**data/processor.py — PCR 전처리 추가**:
```python
# clean_fundamentals()에 PCR 전처리 블록 추가
# PCR: 0 이하 → NaN (영업현금흐름 마이너스 = 배제) + 상위 1% Winsorize
if "PCR" in cleaned.columns:
    cleaned["PCR"] = cleaned["PCR"].where(cleaned["PCR"] > 0, np.nan)
    upper = cleaned["PCR"].quantile(0.99)
    cleaned["PCR"] = cleaned["PCR"].clip(upper=upper)
```

**테스트**: `tests/test_factors.py` 업데이트
- PCR 양수일 때 역수 변환 + 순위 정상 동작
- PCR 0 이하 종목 제외 확인
- PCR 결측 시 PBR+DIV 가중치 재분배 확인

---

### 1-2. Quality 팩터 — GP/A + EY + F-Score

**파일**: `factors/quality.py`

**변경 사항**:
```python
# 기존: ROE(40%) + EY(30%) + 배당(30%)
# 신규: GP/A(40%) + EY(30%) + F-Score(30%)

# GP/A = 매출총이익 / 총자산
# GP/A가 높을수록 수익성 우수
# Novy-Marx (2013): Value 팩터와 음의 상관 → 분산 효과
```

**핵심 변경 — _calc_gpa_score() 신규 메서드**:
```python
@staticmethod
def _calc_gpa_score(fundamentals: pd.DataFrame) -> pd.Series:
    """GP/A = 매출총이익 / 총자산 순위 스코어
    
    데이터 소스: DART 손익계산서(매출총이익) + 재무상태표(총자산)
    매출총이익 없으면: 매출액 - 매출원가로 계산
    """
    pass
```

**F-Score 결정**:
- DART에서 전기 재무제표를 안정적으로 가져올 수 있으면 → 7점 F-Score 구현
- 가져올 수 없으면 → F-Score 제거, GP/A(60%) + EY(40%)로 재분배
- **첫 번째 커밋에서는 F-Score 없이 GP/A+EY만으로 시작하고, DART 전기 데이터 확보 후 F-Score 추가 권장**

**기존 _calc_dividend_score() 제거 이유**:
배당은 이미 Value 팩터의 DIV에 포함. Quality에서도 넣으면 삼중 가중 문제.
(기존 v1.1에서 Value DIV 20% + Quality 배당 30% + 고배당 프리셋 D의 DIV 50% = 삼중)

**데이터 확보 (data/dart_client.py 추가)**:
```python
def get_gross_profit(self, corp_code: str, year: int) -> Optional[float]:
    """DART 손익계산서에서 매출총이익 조회"""
    pass

def get_total_assets(self, corp_code: str, year: int) -> Optional[float]:
    """DART 재무상태표에서 총자산 조회"""
    pass
```

**테스트**: `tests/test_factors.py` 업데이트
- GP/A 정상 계산 + 순위 스코어 변환
- 총자산 0 이하 종목 제외
- EY(1/PER) 기존 로직 유지 확인
- GP/A 결측 시 EY만으로 가중치 재분배

---

### 1-3. Momentum 팩터 — 유효 데이터 기준 강화

**파일**: `factors/momentum.py` + `data/collector.py`

**변경 사항 (collector.py)**:
```python
# 기존: counts >= 10
# 신규: counts >= lookback_trading_days * 0.7

# 12M 모멘텀 → lookback ≈ 252일 → 최소 176일 데이터 필요
# 6M 모멘텀 → lookback ≈ 126일 → 최소 88일 데이터 필요
```

**변경 위치**: `ReturnCalculator.get_returns_bulk()` (약 866행)
```python
# 기존
valid_mask = (counts >= 10) & (first_prices > 0)

# 신규
min_required = max(int(lookback_trading_days * 0.7), 20)  # 최소 20일은 보장
valid_mask = (counts >= min_required) & (first_prices > 0)
```

동일 로직을 `get_returns_multi_period()`에도 적용.

---

### 1-4. 팩터 상관관계 검증 (Phase 1 완료 후 필수)

**파일**: `notebooks/factor_correlation.py` (신규 스크립트)

**목적**: PER → PCR 교체의 핵심 이유가 Quality의 EY(1/PER)와의 이중 가중 제거인데,
실제로 교체 후 팩터 간 독립성이 개선됐는지 수치로 확인해야 한다.

```python
def check_factor_correlation(value_score, momentum_score, quality_score):
    """3개 팩터 스코어 간 피어슨 상관계수 매트릭스
    
    판단 기준:
    - |상관계수| < 0.3: 양호 (독립적)
    - |상관계수| 0.3~0.5: 주의 (약한 상관)
    - |상관계수| > 0.5: 경고 (이중 가중 가능성)
    """
    df = pd.DataFrame({
        'value': value_score,
        'momentum': momentum_score,
        'quality': quality_score,
    })
    corr = df.corr()
    
    # v1.1 대비 개선 확인: Value-Quality 상관이 낮아졌는지
    vq_corr = abs(corr.loc['value', 'quality'])
    if vq_corr > 0.5:
        logger.warning(f"Value-Quality 상관 {vq_corr:.2f} — 이중 가중 잔존 가능")
    else:
        logger.info(f"Value-Quality 상관 {vq_corr:.2f} — 독립성 양호")
    
    return corr
```

**실행 시점**: Phase 1 커밋 전, 임의의 날짜(예: 20240630)로 스크리너를 돌려서
3개 팩터 스코어를 뽑고 상관 매트릭스 확인. Value-Quality 상관이 0.5 초과면 팩터 구성 재검토.

---

## Phase 2: 스크리너 + 백테스트 개선 (3~5일)

### 2-1. Reporting Lag 처리

**파일**: `strategy/screener.py`

**변경 사항**:
```python
def _get_effective_fundamental_date(self, rebalance_date: str) -> str:
    """리밸런싱 날짜 기준으로 사용 가능한 재무 데이터 날짜 결정
    
    12월 결산 기업 기준:
    - 1~3월 리밸런싱: 전전년도 연간 보고서 사용
    - 4~12월 리밸런싱: 전년도 연간 보고서 사용
    
    분기 보고서 활용 시:
    - 각 분기 실적 발표 + 45일 이후부터 사용
    """
    pass
```

**screen() 메서드 수정**:
```python
# 기존: get_fundamentals_all(date, market)
# 신규: get_fundamentals_all(effective_date, market)
#       effective_date = _get_effective_fundamental_date(date)
```

### 2-2. 생존자 편향 폴백 강화

**파일**: `data/collector.py`의 `get_universe()`

**변경 사항**:
```python
# 기존: KRX API 실패 시 빈 DataFrame 반환
# 신규: KRX API 실패 시 직전 성공 유니버스를 DB에서 로드

def get_universe(self, date: str, market: str = "KOSPI") -> pd.DataFrame:
    # 1. KRX Open API 시도
    # 2. 실패 시 → DB에서 해당 시장의 가장 최근 유니버스 로드
    # 3. 그것도 없으면 → 빈 DataFrame (최후의 수단)
    pass
```

### 2-3. Walk-Forward 백테스트 — 기존 메서드 교체 (신규 아님)

**파일**: `backtest/engine.py`

> ⚠️ 기존 `walk_forward()` 메서드(745행)가 이미 존재한다.
> 하지만 현재 구현은 "전체 기간을 n등분 → 각 조각 내 70:30 분할"이라
> 학습 기간이 너무 짧아지는 문제가 있다.
> **기존 메서드를 삭제하고 슬라이딩 윈도우 방식으로 교체한다.**

**변경 사항 — 기존 `walk_forward()` 교체**:
```python
def run_walk_forward(
    self,
    full_start: str,      # 전체 기간 시작
    full_end: str,         # 전체 기간 끝
    train_years: int = 4,  # 학습 기간 (년)
    test_years: int = 2,   # 검증 기간 (년)
    market: str | None = None,
) -> pd.DataFrame:
    """Walk-Forward 백테스트 실행 (슬라이딩 윈도우)
    
    기존 walk_forward()를 교체.
    4~5년 학습 → 2년 검증 윈도우를 2년씩 슬라이딩.
    각 윈도우의 검증 성과를 이어붙여 반환.
    """
    pass
```

**첫 구현에서는 파라미터 최적화 없이**, 고정 파라미터로 여러 윈도우를 슬라이딩하며
검증 성과를 측정하는 것만 구현. 파라미터 자동 최적화는 Phase 5에서 추가.

### 2-4. 무위험 수익률 상수 → 동적 참조

**파일**: `backtest/metrics.py`

**문제**: 12행의 `RF_ANNUAL: float = settings.momentum.risk_free_rate`가
모듈 임포트 시점에 고정돼서, 프리셋별로 다른 risk_free_rate를 사용해도
Sharpe 계산에 반영되지 않는다.

**변경 사항**:
```python
# 기존 (12행 — 삭제)
RF_ANNUAL: float = settings.momentum.risk_free_rate

# 신규 — calculate_sharpe()에서 매번 읽기
def calculate_sharpe(
    self, returns: pd.Series, risk_free: float | None = None
) -> float:
    if risk_free is None:
        risk_free = settings.momentum.risk_free_rate
    # ... 기존 로직 ...
```

### 2-5. 배당 추정 제거

**파일**: `backtest/engine.py`

**결정**: 한국 시장은 12월 결산 기업이 대부분이라 배당이 연 1회(3~4월 지급) 집중됨.
기존의 `_estimate_dividend_income()`은 미국식 월별 균등 배분이라 현실과 괴리가 크다.
잘못된 배당 추정이 백테스트 수익률을 왜곡하므로 **제거하고 보수적 추정으로 전환**한다.

**변경 사항**:
```python
# 기존 (engine.py run() 메서드 내, 약 167행)
cash = self._estimate_dividend_income(
    holdings, prices, date_str, market, cash,
)

# 신규 — 해당 호출 제거 (주석으로 이유 명시)
# 배당금 추정 제거 (v2.0):
# 한국 시장은 연 1회 배당 집중 → 월별 균등 배분은 부정확.
# 백테스트 수익률에 배당은 미포함 (보수적 추정).
# 실전 운용에서는 키움 API 잔고 조회 시 배당금이 자동 반영됨.
```

**_estimate_dividend_income() 메서드**: 삭제하지 않고 `@deprecated` 표시.
향후 DART 배당락일 데이터를 활용한 정확한 배당 반영 시 재활용 가능.

---

## Phase 3: 프리셋 정리 (1~2일)

### 3-1. config.yaml 재작성

**기존 9+7 프리셋 → 4+4 프리셋으로 교체**
- `docs/PRD_v2.md`의 Section 3.2, 3.3 정의를 그대로 YAML로 변환
- 기존 프리셋(A~I, 100만~5억)은 주석 블록으로 보존 (롤백 가능)

### 3-2. settings.py 수정

1. **ValueWeights**: `per` → `pcr`
2. **프리셋 충돌 감지**: `_apply_yaml()`에서 금액 프리셋이 STRATEGY_ONLY_KEYS를 변경하면 WARNING + 무시
3. **null 비활성화 지원**: `max_drawdown_pct: null` → 서킷브레이커 비활성화 (0.99 패턴 제거)
4. **validate_settings()**: value_weights 합계 검증에서 `per` → `pcr` 반영

### 3-3. 프리셋 충돌 감지 구현

```python
STRATEGY_ONLY_KEYS = {
    "factor_weights", "value_weights", "momentum", "quality",
    "volatility", "market_regime",
}
STRATEGY_ONLY_TRADING_KEYS = {
    "max_drawdown_pct", "vol_target", "trailing_stop_pct", "max_turnover_pct",
}

def _apply_yaml(settings_obj, data):
    # ... 기존 로직 ...
    # 2단계: 금액 프리셋 적용 시 충돌 검사
    if sizing_name and presets and sizing_name in presets:
        sizing_data = presets[sizing_name]
        for key in sizing_data:
            if key in STRATEGY_ONLY_KEYS:
                logger.warning(
                    f"금액 프리셋 '{sizing_name}'이 전략 전용 키 '{key}'를 "
                    f"포함합니다. 무시합니다."
                )
                # 해당 키를 sizing_data에서 제거하고 적용
```

---

## Phase 4: 통합 테스트 (2~3일)

### 4-0. 코드 정리 (테스트 전 선행)

**vol_target 중복 제거**:

`scheduler/main.py`의 `_calc_vol_target_scale()` (62행)과
`backtest/engine.py`의 `_calc_vol_target_scale()` (984행)이 동일 로직 중복.
하나를 고칠 때 다른 하나를 놓치는 문제를 방지한다.

```python
# 방법: strategy/market_regime.py에 공통 함수로 추출
# market_regime.py에 추가:
def calc_vol_target_scale(
    recent_values: list[float],
    vol_target: float,
    lookback: int,
) -> float:
    """변동성 타겟팅 — 실현 변동성 대비 투자 비중 배율 계산"""
    pass

# engine.py, scheduler/main.py에서 이 함수를 import하여 사용
```

**screener 캐시 메모리 제한**:

`strategy/screener.py`의 `_factor_cache` (30행)가 클래스 변수 dict로
장기 백테스트 시 메모리가 선형 증가. TTL 또는 maxsize 제한 추가.

```python
# 방법 1: functools.lru_cache (간단, 단 hashable key 필요)
# 방법 2: 수동 maxsize 제한
class MultiFactorScreener:
    _factor_cache: dict[tuple[str, str], pd.DataFrame] = {}
    _CACHE_MAX_SIZE: int = 24  # 최근 24개월분만 보관

    def _cache_put(self, key, value):
        if len(self._factor_cache) >= self._CACHE_MAX_SIZE:
            oldest = next(iter(self._factor_cache))
            del self._factor_cache[oldest]
        self._factor_cache[key] = value
```

### 4-1. 새 팩터 → 기존 인프라 연결 확인

```bash
# 테스트 순서
pytest tests/test_factors.py -v        # 팩터 단위
pytest tests/test_screener.py -v       # 스크리너 통합
pytest tests/test_backtest.py -v       # 백테스트 엔진
pytest tests/test_settings.py -v       # 프리셋 충돌 감지
pytest tests/test_integration.py -v    # E2E
```

### 4-2. 전 기간 백테스트 (기준선 확보)

```bash
# 4개 전략 프리셋 × 전 기간(2015~2024) 백테스트
python run_backtest.py --preset A --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset B --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset C --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset D --start 2015-01-01 --end 2024-12-31
```

### 4-3. Walk-Forward 검증

```bash
python run_backtest.py --preset A --walk-forward --train-years 4 --test-years 2
```

---

## Phase 5: 파라미터 Grid Search (선택, 3~5일)

이 Phase는 Phase 4의 기준선 결과를 본 후 진행 여부를 결정.
기준선이 KPI 목표("최소 통과")를 충족하면 Phase 6으로 직행.

### 탐색 대상

| 파라미터 | 범위 | 스텝 |
|---------|------|------|
| V 가중치 | 0.2 ~ 0.5 | 0.05 |
| M 가중치 | 0.2 ~ 0.5 | 0.05 |
| 종목 수 | 10, 15, 20, 25, 30 | — |
| 시총 하한 percentile | 5, 10, 15, 20 | — |
| 변동성 필터 percentile | 60, 70, 80, 90 | — |

### 탐색 방법

```python
# 한 번에 파라미터 1개씩만 변경 (나머지 고정)
# 전 기간 백테스트 → CAGR, MDD, Sharpe 기록
# 최적값의 ±10%에서 Sharpe 변동 20% 이내인지 확인 (인접 안정성)
# Walk-Forward로 재검증
```

---

## Phase 6: 최종 확정 (1일)

1. 4개 프리셋의 최종 파라미터 → config.yaml 반영
2. `run_backtest.py`에 전체 프리셋 비교 리포트 생성 기능 추가
3. CLAUDE.md 수술 체크리스트 완료 처리
4. Git tag: `v2.0-strategy-redesign`
5. 모의투자 테스트 계획 갱신 (`docs/11_mock_trading_test_plan.md`)

---

## 커밋 전략

```
Phase 1 완료 → git commit -m "refactor(factors): PER→PCR, ROE→GP/A, 유효데이터 강화, PCR전처리, 상관검증"
Phase 2 완료 → git commit -m "feat(engine): Reporting Lag, Walk-Forward 교체, RF동적참조, 배당추정 제거, 생존편향 폴백"
Phase 3 완료 → git commit -m "refactor(config): 4+4 프리셋, 충돌감지, null 비활성화"
Phase 4 완료 → git commit -m "fix(infra): vol_target 중복제거, 캐시메모리 제한, 통합테스트 + Walk-Forward 기준선"
Phase 5 완료 → git commit -m "tune: Grid Search + 인접안정성 검증"
Phase 6 완료 → git tag v2.0-strategy-redesign
```

---

## 롤백 계획

수술이 실패하면 (새 팩터 성과가 기존보다 유의미하게 나쁠 경우):

1. `git stash` 또는 브랜치로 수술 코드 보관
2. `main` 브랜치의 v1.1 코드로 복귀
3. **부분 채택 가능**: 아래 항목은 팩터 성과와 무관하게 독립적으로 적용 가능
   - Walk-Forward 백테스트
   - Reporting Lag 처리
   - 프리셋 4+4 정리 (기존 팩터 가중치 유지하면서)
   - 배당 추정 제거 (보수적 추정으로 전환)
   - RF_ANNUAL 동적 참조
   - vol_target 중복 제거
   - screener 캐시 메모리 제한

> 핵심: PCR/GP/A 팩터 교체가 성과를 악화시킬 가능성은 낮지만 (학술적 근거 있음),
> 한국 시장 특화 데이터에서 실증되지 않은 요소이므로 백테스트 결과를 반드시 확인.
