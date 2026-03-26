# Claude Code CLI 수술 프롬프트 전체 가이드

> **사용법**: 각 프롬프트를 Claude Code CLI에 순서대로 입력.
> 각 단계 완료 후 테스트 통과를 확인하고 커밋한 뒤 다음 단계로 진행.
> ⚠️ CLAUDE.md는 프로젝트 루트에 있으면 자동으로 읽힘 — 별도 언급 불필요.

---

## 사전 준비 (1회)

```
git checkout -b v2.0-surgery
```

---

## Phase 1: 팩터 재구축

### Phase 1-1: Value 팩터 — PER → PCR 교체

```
docs/PRD_v2.md와 docs/SURGERY_GUIDE.md를 읽어줘.

SURGERY_GUIDE Phase 1-1을 진행해.
Value 팩터에서 PER을 제거하고 PCR(주가현금흐름비율)로 교체하는 작업이야.

수정 대상 파일과 구체적 변경사항:

1. config/settings.py
   - ValueWeights 데이터클래스: per → pcr 필드명 변경 (기본값 0.30 유지)
   - validate_settings()에서 value_weights 합계 검증 필드도 per → pcr로 변경

2. factors/value.py
   - PER 스코어 계산 블록을 PCR 스코어로 교체
   - PCR이 낮을수록 저평가 → 역수 변환 후 순위 스코어 (PER과 동일 로직)
   - PCR <= 0 (영업현금흐름 마이너스) 종목은 제외
   - self.w.per 참조를 self.w.pcr로 변경

3. data/processor.py
   - clean_fundamentals()에 PCR 전처리 블록 추가:
     PCR 0 이하 → NaN, 상위 1% Winsorize (PBR/PER과 동일 패턴)

4. data/dart_client.py
   - get_operating_cashflow(corp_code, year) 메서드 추가
     DART 현금흐름표에서 영업활동현금흐름 조회
   - calc_pcr(ticker, date) 메서드 추가
     PCR = 현재가 / (영업활동현금흐름 / 발행주식수)

5. data/storage.py
   - Fundamental ORM 모델에 pcr = Column(Float) 추가

6. config/config.yaml
   - 모든 프리셋의 value_weights에서 per → pcr 키 변경 (값은 그대로)
   - 주석 처리된 개별 설정 예시에서도 per → pcr 변경

7. tests/test_factors.py 업데이트
   - PCR 양수일 때 역수 변환 + 순위 정상 동작 테스트
   - PCR 0 이하 종목 제외 확인 테스트
   - PCR 결측 시 PBR+DIV 가중치 재분배 확인 테스트 (weighted_average_nan_safe)

완료 후 아래 테스트를 실행해서 결과를 보여줘:
pytest tests/test_factors.py -v -k "value or Value"
pytest tests/test_settings.py -v
```

### Phase 1-2: Quality 팩터 — ROE → GP/A, 배당 스코어 제거

```
docs/SURGERY_GUIDE.md Phase 1-2를 진행해.
Quality 팩터를 전면 재설계하는 작업이야.

기존: ROE(40%) + Earnings Yield(30%) + 배당(30%)
신규: GP/A(40%) + Earnings Yield(30%) + F-Score(30%)

수정 대상 파일과 구체적 변경사항:

1. factors/quality.py
   - _calc_roe_score() → _calc_gpa_score()로 교체
     GP/A = 매출총이익 / 총자산
     총자산 <= 0인 종목 제외
     상하위 1% Winsorize 후 순위 스코어(0~100)
   
   - _calc_dividend_score() 제거
     이유: 배당은 이미 Value 팩터 DIV에 포함. 삼중 가중 방지.
   
   - _calc_earnings_yield_score()는 유지 (1/PER, 기존 그대로)
   
   - calculate() 메서드의 가중치 변경:
     기존: roe(0.40), earnings_yield(0.30), dividend(0.30)
     신규: gpa(0.40), earnings_yield(0.30), fscore(0.30)
   
   - F-Score 30% 가중치 반영:
     calc_fscore()의 결과를 0~100으로 정규화해서 score_parts에 추가.
     현재 calc_fscore()는 0~5 정수 반환 → (fscore / max_score * 100)으로 변환.
     F-Score 데이터가 없으면 GP/A(60%) + EY(40%)로 자동 재분배
     (기존 weighted_average_nan_safe가 NaN 팩터 가중치를 재분배하므로 별도 로직 불필요)

2. data/dart_client.py
   - get_gross_profit(corp_code, year) 메서드 추가
     DART 손익계산서에서 매출총이익 조회
     매출총이익 없으면 매출액 - 매출원가로 계산
   - get_total_assets(corp_code, year) 메서드 추가
     DART 재무상태표에서 총자산 조회

3. tests/test_factors.py 업데이트
   - GP/A 정상 계산 + 순위 스코어 변환 테스트
   - 총자산 0 이하 종목 제외 테스트
   - EY(1/PER) 기존 로직 유지 확인 테스트
   - GP/A 결측 시 EY만으로 가중치 재분배 테스트
   - F-Score 정규화(0~100) 테스트
   - 기존 _calc_dividend_score 관련 테스트 제거 또는 수정

완료 후:
pytest tests/test_factors.py -v -k "quality or Quality"
```

### Phase 1-3: Momentum 팩터 — 유효 데이터 기준 강화

```
docs/SURGERY_GUIDE.md Phase 1-3을 진행해.
모멘텀 수익률 계산 시 유효 데이터 최소 기준을 강화하는 작업이야.

기존: counts >= 10 (12개월 모멘텀인데 10일 데이터만 있어도 통과)
신규: counts >= lookback_trading_days × 0.7 (12M → 최소 176일, 6M → 최소 88일)

수정 대상:

1. data/collector.py — ReturnCalculator.get_returns_bulk() (약 866행 부근)
   기존: valid_mask = (counts >= 10) & (first_prices > 0)
   신규:
   ```python
   # lookback 기간에 해당하는 영업일 수 계산
   lookback_trading_days = int(lookback_months * 21)  # 월당 약 21영업일
   min_required = max(int(lookback_trading_days * 0.7), 20)  # 최소 20일 보장
   valid_mask = (counts >= min_required) & (first_prices > 0)
   ```

2. data/collector.py — ReturnCalculator.get_returns_multi_period()
   동일한 로직을 적용. 각 lookback_months별로 min_required를 별도 계산.
   프리페치 후 벌크 재조회에서도 동일 기준 적용 (기존 counts >= 2 → min_required).

3. tests/test_collector.py 업데이트
   - 데이터가 충분한 종목만 수익률 계산에 포함되는지 테스트
   - 데이터 부족 종목이 정상 제외되는지 테스트

완료 후:
pytest tests/test_collector.py -v
pytest tests/test_factors.py -v -k "momentum or Momentum"
```

### Phase 1-4: 팩터 상관관계 검증

```
docs/SURGERY_GUIDE.md Phase 1-4를 진행해.
Phase 1-1 ~ 1-3에서 교체한 팩터들의 독립성을 수치로 검증하는 작업이야.

notebooks/factor_correlation.py 파일을 새로 생성해줘.

이 스크립트는:
1. strategy/screener.py의 MultiFactorScreener를 사용해서
   임의의 날짜(예: "20240628")에 대해 스크리닝을 실행
2. composite_df에서 value_score, momentum_score, quality_score 3개 컬럼 추출
3. 3×3 피어슨 상관계수 매트릭스를 계산하고 출력
4. 판단 기준:
   - |상관계수| < 0.3: "양호 (독립적)"
   - |상관계수| 0.3~0.5: "주의 (약한 상관)"
   - |상관계수| > 0.5: "경고 (이중 가중 가능성)"
5. 특히 Value-Quality 상관이 v1.1 대비 개선됐는지가 핵심.
   0.5를 초과하면 팩터 교체가 목적을 달성하지 못한 것.

스크립트 하단에 if __name__ == "__main__": 블록으로
직접 실행 가능하게 만들어줘.
sys.path에 프로젝트 루트를 추가하는 것도 잊지 마.

생성 후 실행 가능한지 확인해줘 (실제 데이터가 없으면 mock으로 테스트).
```

### Phase 1 커밋

```
Phase 1 변경사항을 확인하고 커밋해줘.

전체 테스트 실행:
pytest tests/test_factors.py tests/test_settings.py tests/test_collector.py -v

모두 통과하면:
git add -A
git commit -m "refactor(factors): PER→PCR, ROE→GP/A, 유효데이터 강화, PCR전처리, 상관검증"
```

---

## Phase 2: 스크리너 + 백테스트 개선

### Phase 2-1: Reporting Lag 처리

```
docs/SURGERY_GUIDE.md Phase 2-1을 진행해.
재무제표 발표 지연(Reporting Lag)을 처리해서 Look-Ahead Bias를 차단하는 작업이야.

수정 대상:

1. strategy/screener.py
   - _get_effective_fundamental_date(self, rebalance_date: str) -> str 메서드 신규 추가
   
   로직:
   - rebalance_date의 월(month)을 확인
   - 1~3월: 전전년도 12월 결산 데이터 사용 (전년도 연간 보고서 미공시)
   - 4~12월: 전년도 12월 결산 데이터 사용 (3월 말까지 공시 완료 가정)
   - 반환값: 사용 가능한 재무 데이터의 기준 날짜 (YYYYMMDD 형식)
     예: rebalance_date="20240229" → 반환 "20221231" (전전년도)
     예: rebalance_date="20240430" → 반환 "20231231" (전년도)
   
   - screen() 메서드에서 fundamentals 조회 시 effective_date 사용:
     기존: self.collector.get_fundamentals_all(date, m)
     신규: effective_date = self._get_effective_fundamental_date(date)
           self.collector.get_fundamentals_all(effective_date, m)
   
   주의: 모멘텀(가격 데이터)에는 Reporting Lag를 적용하지 않음.
   가격 데이터는 실시간이므로 원래 date를 그대로 사용.

2. tests/test_screener.py 업데이트
   - 1~3월 리밸런싱 시 전전년도 데이터 사용 확인
   - 4월 리밸런싱 시 전년도 데이터 사용 확인
   - 12월 리밸런싱 시 전년도 데이터 사용 확인

완료 후:
pytest tests/test_screener.py -v
```

### Phase 2-2: 생존자 편향 폴백 강화

```
docs/SURGERY_GUIDE.md Phase 2-2를 진행해.
KRX API 실패 시 빈 DataFrame 대신 직전 성공 유니버스를 사용하도록 개선하는 작업이야.

수정 대상:

1. data/collector.py — get_universe() 메서드
   
   현재 동작: KRX API 실패 → 빈 DataFrame 반환 → 해당 월 스킵
   
   변경 후 동작:
   a. KRX Open API에서 유니버스 조회 시도
   b. 실패 시 → DB에서 같은 시장의 가장 최근 유니버스 로드
      (storage에 유니버스 저장 로직이 있는지 확인하고, 없으면 추가)
   c. DB에서도 못 찾으면 → 빈 DataFrame 반환 (최후의 수단)
   d. 폴백 사용 시 logger.warning으로 명시
   
   구현 힌트:
   - 유니버스 성공 조회 시 DB에 저장: storage.save_universe(date, market, df)
   - 폴백 시 조회: storage.load_latest_universe(market) → 가장 최근 날짜의 유니버스

2. data/storage.py (필요 시)
   - Universe ORM 모델 추가 (또는 기존 테이블 활용)
   - save_universe(), load_latest_universe() 메서드 추가

3. tests/test_collector.py 업데이트
   - KRX API 실패 시 DB 폴백 동작 테스트 (mock)

완료 후:
pytest tests/test_collector.py -v
```

### Phase 2-3: Walk-Forward 기존 메서드 교체

```
docs/SURGERY_GUIDE.md Phase 2-3을 진행해.
기존 walk_forward() 메서드를 슬라이딩 윈도우 방식으로 교체하는 작업이야.

수정 대상:

1. backtest/engine.py
   - 기존 walk_forward() 메서드 (745행~862행)를 삭제
   - run_walk_forward()로 교체
   
   기존 문제: 전체 기간을 n등분 → 각 조각 내 70:30 분할 → 학습 기간이 너무 짧음
   
   새 설계 (PRD_v2.md Section 4.2):
   ```
   [2010-2014 학습] → [2015-2016 검증] → 성과 기록
   [2012-2016 학습] → [2017-2018 검증] → 성과 기록
   [2014-2018 학습] → [2019-2020 검증] → 성과 기록
   [2016-2020 학습] → [2021-2022 검증] → 성과 기록
   [2018-2022 학습] → [2023-2024 검증] → 성과 기록
   ```
   
   시그니처:
   def run_walk_forward(
       self,
       full_start: str,       # "2010-01-01"
       full_end: str,          # "2024-12-31"
       train_years: int = 4,
       test_years: int = 2,
       step_years: int = 2,    # 윈도우 이동 간격
       market: str | None = None,
   ) -> list[dict]:
   
   - 각 윈도우에서 self.run(train_start, train_end, market)과
     self.run(test_start, test_end, market)을 실행
   - PerformanceAnalyzer로 각 구간의 CAGR, MDD, Sharpe 계산
   - 전체 윈도우 요약: 평균 Test CAGR, 과적합 갭(Train-Test CAGR 차이)
   - 결과를 list[dict]로 반환 (기존 반환 형식 유지)

2. run_backtest.py
   - --walk-forward 플래그 추가
   - --train-years, --test-years 옵션 추가
   - 기존 walk_forward() 호출 부분이 있으면 run_walk_forward()로 변경

3. tests/test_backtest.py 업데이트
   - 기존 walk_forward 테스트를 run_walk_forward로 변경
   - 윈도우가 올바르게 슬라이딩되는지 테스트 (날짜 계산)
   - 학습/검증 기간이 겹치지 않는지 확인

완료 후:
pytest tests/test_backtest.py -v -k "walk"
```

### Phase 2-4: 무위험 수익률 동적 참조

```
docs/SURGERY_GUIDE.md Phase 2-4를 진행해.
metrics.py의 RF_ANNUAL 상수를 동적 참조로 변경하는 작업이야.

수정 대상:

1. backtest/metrics.py
   - 12행의 RF_ANNUAL 모듈 레벨 상수 삭제:
     기존: RF_ANNUAL: float = settings.momentum.risk_free_rate
   
   - calculate_sharpe() 시그니처 변경:
     기존: def calculate_sharpe(self, returns, risk_free=RF_ANNUAL)
     신규: def calculate_sharpe(self, returns, risk_free: float | None = None)
     
     메서드 내부에서:
     if risk_free is None:
         risk_free = settings.momentum.risk_free_rate
   
   - RF_ANNUAL을 참조하는 다른 곳이 있으면 동일하게 변경
     (grep으로 RF_ANNUAL 사용처 전체 확인)

2. tests/test_backtest.py
   - Sharpe 계산 테스트에서 risk_free 파라미터 명시적 전달 확인
   - settings.momentum.risk_free_rate 변경 후 Sharpe가 달라지는지 테스트

완료 후:
pytest tests/test_backtest.py -v -k "sharpe or Sharpe or metric"
```

### Phase 2-5: 배당 추정 제거

```
docs/SURGERY_GUIDE.md Phase 2-5를 진행해.
백테스트에서 배당 추정 로직을 비활성화하는 작업이야.

이유: 한국 시장은 12월 결산 기업이 대부분이라 배당이 연 1회(3~4월) 집중됨.
기존의 월별 균등 배분(연간 배당 / 12)은 현실과 괴리가 크고 백테스트 수익률을 왜곡함.

수정 대상:

1. backtest/engine.py — run() 메서드 내
   - _estimate_dividend_income() 호출 부분 (약 167~170행) 제거
   - 해당 위치에 주석으로 이유 명시:
     ```python
     # 배당금 추정 제거 (v2.0):
     # 한국 시장은 연 1회 배당 집중 → 월별 균등 배분은 부정확.
     # 백테스트 수익률에 배당 미포함 (보수적 추정).
     # 실전에서는 키움 API 잔고 조회 시 배당금 자동 반영.
     ```

2. backtest/engine.py — _estimate_dividend_income() 메서드
   - 삭제하지 않고 유지하되, docstring 첫 줄에 DEPRECATED 표시:
     ```python
     def _estimate_dividend_income(self, ...):
         """[DEPRECATED v2.0] 월별 배당금 추정 — 한국 시장에 부적합
         
         v2.0에서 비활성화됨. 향후 DART 배당락일 데이터를 활용한
         정확한 배당 반영 시 재활용 가능.
         
         기존 문서: ...
         """
     ```

3. tests/test_backtest.py
   - 배당 관련 테스트가 있으면 DEPRECATED 표시하거나 스킵 처리
   - 배당 미포함 상태에서 백테스트 수익률 계산이 정상인지 확인

완료 후:
pytest tests/test_backtest.py -v
```

### Phase 2 커밋

```
Phase 2 전체 변경사항을 확인하고 커밋해줘.

전체 테스트 실행:
pytest tests/ -v --tb=short

모두 통과하면:
git add -A
git commit -m "feat(engine): Reporting Lag, Walk-Forward 교체, RF동적참조, 배당추정 제거, 생존편향 폴백"
```

---

## Phase 3: 프리셋 정리

### Phase 3-1: config.yaml 재작성

```
docs/PRD_v2.md의 Section 3.2(전략 프리셋 4개)와 Section 3.3(금액 프리셋 4개)을 읽고,
config/config.yaml을 전면 재작성해줘.

핵심 변경:
- 기존 전략 프리셋 A~I (9개) → A, B, C, D (4개)로 교체
- 기존 금액 프리셋 100만~5억 (7개) → 소액, 중액, 대액, 거액 (4개)로 교체
- PRD_v2.md에 정의된 YAML 블록을 그대로 사용

추가 규칙:
- 기존 프리셋 정의(A~I, 100만~5억)는 파일 하단에 주석 블록으로 보존 (롤백 가능)
- value_weights에서 per → pcr로 키 변경 (Phase 1에서 이미 코드 변경됨)
- max_drawdown_pct와 vol_target에 0.99 패턴 사용 금지.
  비활성화하려면 null 사용 (Phase 3-2에서 코드 지원 추가)
- 파일 상단 주석에 적용 순서 설명: 전략 프리셋 → 금액 프리셋 → 개별 설정 덮어쓰기
- 금액 프리셋은 전략 전용 키(factor_weights, market_regime 등)를 포함하지 않음

기존 preset: "B" / sizing: "1000만" 부분도
preset: "A" / sizing: "중액" 으로 변경해줘.
```

### Phase 3-2: settings.py — 프리셋 충돌 감지 + null 비활성화

```
docs/SURGERY_GUIDE.md Phase 3-2, 3-3을 진행해.
settings.py에 프리셋 충돌 감지와 null 비활성화 지원을 추가하는 작업이야.

수정 대상:

1. config/settings.py — 프리셋 충돌 감지

   모듈 상단에 상수 정의:
   ```python
   STRATEGY_ONLY_KEYS = {
       "factor_weights", "value_weights", "momentum", "quality",
       "volatility", "market_regime",
   }
   STRATEGY_ONLY_TRADING_KEYS = {
       "max_drawdown_pct", "vol_target", "trailing_stop_pct", "max_turnover_pct",
   }
   ```
   
   _apply_yaml()에서 금액 프리셋 적용(2단계) 시:
   - sizing_data의 각 키가 STRATEGY_ONLY_KEYS에 포함되면
     logger.warning() 출력하고 해당 키를 무시 (적용하지 않음)
   - sizing_data의 trading 섹션 내 키가 STRATEGY_ONLY_TRADING_KEYS에 포함되면
     동일하게 경고 + 무시

2. config/settings.py — null 비활성화 지원

   TradingConfig의 max_drawdown_pct와 vol_target 필드 타입을 Optional[float]로 변경:
   ```python
   max_drawdown_pct: Optional[float] = 0.25
   vol_target: Optional[float] = 0.15
   ```
   
   validate_settings()에서:
   - None인 경우 범위 검증 스킵 (비활성화 허용)
   - 0.99 값이면 WARNING 출력: "0.99 대신 null을 사용하세요"

3. backtest/engine.py
   - _apply_circuit_breaker()에서 max_drawdown_pct가 None이면 서킷브레이커 스킵
   - _calc_vol_target_scale()에서 vol_target이 None이면 1.0 반환

4. tests/test_settings.py 업데이트
   - 금액 프리셋이 전략 전용 키를 덮어쓰려 할 때 경고 + 무시 확인
   - max_drawdown_pct: null 일 때 검증 통과 확인
   - vol_target: null 일 때 검증 통과 확인
   - 0.99 값에 대한 WARNING 확인

완료 후:
pytest tests/test_settings.py -v
pytest tests/test_backtest.py -v -k "circuit or vol_target"
```

### Phase 3 커밋

```
Phase 3 변경사항을 확인하고 커밋해줘.

전체 테스트 실행:
pytest tests/ -v --tb=short

모두 통과하면:
git add -A
git commit -m "refactor(config): 4+4 프리셋, 충돌감지, null 비활성화"
```

---

## Phase 4: 통합 테스트

### Phase 4-0: 코드 정리 (vol_target 중복 제거 + 캐시 제한)

```
docs/SURGERY_GUIDE.md Phase 4-0을 진행해.
테스트 전에 코드 중복과 메모리 이슈를 정리하는 작업이야.

1. vol_target 중복 제거
   
   현재 동일한 변동성 타겟팅 로직이 두 곳에 있어:
   - backtest/engine.py _calc_vol_target_scale() (984행)
   - scheduler/main.py _calc_vol_target_scale() (62행)
   
   strategy/market_regime.py에 공통 함수로 추출:
   ```python
   def calc_vol_target_scale(
       recent_values: list[float],
       vol_target: float | None,
       lookback: int,
   ) -> float:
       """변동성 타겟팅 — 실현 변동성 대비 투자 비중 배율
       
       Args:
           recent_values: 최근 N일 포트폴리오 가치 리스트
           vol_target: 목표 연환산 변동성 (None이면 1.0 반환)
           lookback: 변동성 계산 기간 (거래일)
       
       Returns:
           투자 비중 배율 (0.2 ~ 1.0)
       """
   ```
   
   engine.py와 scheduler/main.py에서 이 함수를 import하여 사용.
   기존 두 메서드의 로직을 병합하되 engine.py 버전을 기준으로 함.

2. screener 캐시 메모리 제한
   
   strategy/screener.py의 _factor_cache (30행):
   - 클래스 변수에 _CACHE_MAX_SIZE = 24 추가
   - 캐시 저장 시 크기 체크: len >= MAX이면 가장 오래된 항목 삭제
   - 별도 _cache_put() 메서드로 분리

3. tests/test_market_regime.py 업데이트
   - calc_vol_target_scale 공통 함수 테스트 추가

완료 후:
pytest tests/test_market_regime.py tests/test_scheduler.py tests/test_screener.py -v
```

### Phase 4-1: 전체 테스트 스위트 실행

```
전체 테스트를 실행해서 Phase 1~3 + 4-0의 모든 변경이 기존 코드와 호환되는지 확인해줘.

pytest tests/ -v --tb=short 2>&1 | head -100

실패하는 테스트가 있으면:
1. 실패 원인 분석
2. Phase 1~3의 변경으로 인한 예상된 실패인지 확인
3. 예상된 실패면 테스트를 새 인터페이스에 맞게 수정
4. 예상 외 실패면 코드 버그 수정

모든 테스트 통과할 때까지 반복해줘.
```

### Phase 4-2: 전 기간 백테스트 기준선 (데이터가 있는 경우만)

```
4개 전략 프리셋으로 전 기간 백테스트를 실행해서 기준선을 확보해줘.

아래 명령을 순서대로 실행하되, 데이터가 없어서 실패하면 오류 메시지를 보여주고 넘어가.
(실제 데이터는 KRX API 키가 있어야 수집 가능하므로, 데이터가 없으면 이 단계는 스킵)

python run_backtest.py --preset A --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset B --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset C --start 2015-01-01 --end 2024-12-31
python run_backtest.py --preset D --start 2015-01-01 --end 2024-12-31

각 프리셋의 CAGR, MDD, Sharpe를 비교 테이블로 정리해줘.
PRD_v2.md의 KPI 목표(Section 5)와 대비하여 통과 여부를 표시해줘.
```

### Phase 4 커밋

```
Phase 4 변경사항을 확인하고 커밋해줘.

pytest tests/ -v --tb=short

모두 통과하면:
git add -A
git commit -m "fix(infra): vol_target 중복제거, 캐시메모리 제한, 통합테스트"
```

---

## Phase 5: 파라미터 Grid Search (선택)

```
이 Phase는 Phase 4의 백테스트 기준선 결과를 확인한 후 진행 여부를 판단해.

만약 기준선이 PRD_v2.md의 KPI "최소 통과" 기준
(CAGR 8%+, MDD -30% 이내, Sharpe 0.6+)을 충족하면 Phase 6으로 직행.

충족하지 못하면 아래 Grid Search를 진행:

docs/PRD_v2.md Section 4.3(파라미터 튜닝 프로토콜)을 읽고,
프리셋 A(균형)에 대해 팩터 가중치 Grid Search를 실행해줘.

탐색 범위:
- Value: 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50
- Momentum: 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50
- Quality: 나머지 (1 - V - M), 단 0.10 이상이어야 함

각 조합에 대해 전 기간 백테스트를 실행하고
CAGR, MDD, Sharpe를 기록해.

결과를 Sharpe Ratio 내림차순으로 정렬하고,
상위 5개 조합에 대해 인접 안정성 확인:
- 최적값의 V ± 0.05, M ± 0.05에서 Sharpe 변동이 20% 이내인지 체크

결과를 docs/grid_search_results.md로 저장해줘.
```

---

## Phase 6: 최종 확정

```
docs/PRD_v2.md와 docs/SURGERY_GUIDE.md를 읽고 최종 확정 작업을 진행해줘.

1. 4개 프리셋의 최종 파라미터가 config/config.yaml에 반영됐는지 확인
   (Phase 5를 진행했으면 Grid Search 결과 반영, 안 했으면 PRD_v2 기본값 유지)

2. CLAUDE.md의 수술 체크리스트를 업데이트:
   모든 Phase 항목에 [x] 표시

3. docs/11_mock_trading_test_plan.md 갱신:
   - 새로운 4개 프리셋 기준으로 모의투자 계획 업데이트
   - 배당 미포함 사항 명시
   - Walk-Forward 결과 참조

4. 최종 전체 테스트:
   pytest tests/ -v

5. 모두 통과하면:
   git add -A
   git commit -m "docs: 수술 완료, 프리셋 확정, 모의투자 계획 갱신"
   git tag v2.0-strategy-redesign
```

---

## 트러블슈팅

### DART API에서 현금흐름/매출총이익을 못 가져올 때

```
Phase 1-1에서 PCR 데이터를 DART에서 못 가져오는 상황이야.

대안으로 PSR(주가매출비율)을 사용하도록 value.py를 수정해줘:
- PSR = 시가총액 / 매출액
- 매출액은 DART에서 거의 모든 기업이 공시하므로 데이터 가용성이 높음
- PSR이 낮을수록 저평가 → 역수 변환 후 순위 스코어 (PCR과 동일 로직)

config/settings.py의 ValueWeights도 pcr → psr로 변경.
config.yaml도 동일하게 반영.
tests도 업데이트.
```

### GP/A 데이터가 불완전할 때

```
Phase 1-2에서 GP/A 계산에 필요한 매출총이익이 일부 기업에서 누락됐어.

두 가지 폴백을 구현해줘:
1. 매출총이익 없으면 → 매출액 - 매출원가로 계산
2. 매출원가도 없으면 → 해당 종목의 GP/A를 NaN으로 처리
   (weighted_average_nan_safe가 자동으로 나머지 팩터로 재분배)

quality.py의 _calc_gpa_score()에 이 폴백 로직을 추가해줘.
```

### 테스트가 대량 실패할 때

```
Phase 1~3 변경 후 기존 테스트가 대량으로 실패하고 있어.

먼저 실패 목록을 보여줘:
pytest tests/ -v --tb=line 2>&1 | grep FAILED

각 실패를 분류해줘:
A. Phase 1~3 변경으로 인한 예상된 실패 (인터페이스 변경)
   → 테스트를 새 인터페이스에 맞게 수정
B. 예상 외 실패 (코드 버그)
   → 원인 분석 후 코드 수정
C. 데이터 의존적 실패 (실제 API 데이터 필요)
   → mock으로 전환하거나 skip 처리

A 유형부터 순서대로 수정하고, 각 수정 후 해당 테스트를 다시 실행해서 통과 확인해줘.
```
