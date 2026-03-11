# 설정 외부화 설계: config.yaml

## 목표

`config/settings.py`에 하드코딩된 전략 파라미터를 `config/config.yaml`로 분리하여 코드 수정 없이 설정 변경 가능하게 한다.

## 결정 사항

- **파일 형식**: YAML (`pyyaml` 의존성 추가)
- **파일 위치**: `config/config.yaml` 단일 파일 (git 추적)
- **비밀정보**: `.env` 유지 (YAML에 포함하지 않음)
- **접근법**: 최소 변경 - dataclass 구조 유지, YAML 로드 함수만 추가
- **유효성 검사**: 상세 검사 (범위, 합계, 허용값 검증 + 친절한 에러 메시지)

## 설정 파일 구조

`config/config.yaml`:

```yaml
# === 팩터 가중치 (합 = 1.0) ===
factor_weights:
  value: 0.40
  momentum: 0.40
  quality: 0.20

# === 밸류 팩터 세부 가중치 ===
value_weights:
  pbr: 0.50
  per: 0.30
  div: 0.20

# === 종목 유니버스 필터 ===
universe:
  market: "KOSPI"
  min_market_cap_percentile: 10.0
  exclude_finance: true
  min_listing_days: 365
  min_avg_trading_value: 100000000

# === 포트폴리오 ===
portfolio:
  n_stocks: 30
  weight_method: "equal"

# === 거래 비용 & 리스크 ===
trading:
  commission_rate: 0.00015
  tax_rate: 0.0018
  slippage: 0.001
  max_position_pct: 0.10
  max_turnover_pct: 0.50
  max_drawdown_pct: 0.30
```

## 로딩 흐름

```
Settings() 생성 시:
  1. dataclass 기본값으로 초기화
  2. config/config.yaml 존재 여부 확인
     → 있으면: YAML 값을 파싱하여 해당 필드 덮어쓰기
     → 없으면: 기본값 유지 (경고 로그 출력)
  3. .env에서 API 키, 경로 등 로드 (기존과 동일)
  4. validate() 호출 → 실패 시 ValueError + 구체적 에러 메시지
```

## 유효성 검사 규칙

| 대상 | 규칙 | 에러 메시지 |
|------|------|-----------|
| factor_weights 합 | = 1.0 (±0.001) | `factor_weights 합이 1.0이 아닙니다: {합}` |
| value_weights 합 | = 1.0 (±0.001) | `value_weights 합이 1.0이 아닙니다: {합}` |
| 비율 필드 (commission_rate 등) | 0.0 ~ 1.0 | `{필드명}는 0~1 범위여야 합니다: {값}` |
| n_stocks | >= 1 정수 | `n_stocks는 1 이상이어야 합니다: {값}` |
| market | KOSPI/KOSDAQ/ALL | `지원하지 않는 market: {값}` |
| weight_method | equal/value_weighted | `지원하지 않는 weight_method: {값}` |
| min_listing_days | >= 0 정수 | `min_listing_days는 0 이상이어야 합니다: {값}` |
| min_avg_trading_value | >= 0 | `min_avg_trading_value는 0 이상이어야 합니다: {값}` |
| min_market_cap_percentile | 0.0 ~ 100.0 | `min_market_cap_percentile은 0~100 범위여야 합니다: {값}` |

## 코드 변경 범위

| 파일 | 변경 |
|------|------|
| `config/settings.py` | `_load_yaml()`, `_apply_yaml()`, `validate()` 함수 추가 (~60줄) |
| `config/config.yaml` | 신규 생성 (주석 포함 기본값) |
| `requirements.txt` | `pyyaml` 추가 |
| `tests/test_settings.py` | 신규 생성 (YAML 로드, 검증, 폴백 테스트) |
| 기존 소스/테스트 파일 | **변경 없음** |

## 영향받지 않는 것

- `settings.factor_weights.value` 등 기존 접근 방식 동일
- `.env` 기반 API 키 로드 동일
- 전역 싱글톤 `settings = Settings()` 동일
- 기존 테스트의 mock 방식 동일
