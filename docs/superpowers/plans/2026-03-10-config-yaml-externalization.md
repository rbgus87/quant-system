# 설정 외부화 (config.yaml) 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `config/settings.py`의 하드코딩된 전략 파라미터를 `config/config.yaml`로 분리하여 코드 수정 없이 설정 변경 가능하게 한다.

**Architecture:** 기존 dataclass 구조 유지. Settings.__post_init__에서 YAML 파일을 로드하고 기본값을 덮어쓴 뒤 validate()로 검증. YAML 파일이 없으면 기본값으로 폴백.

**Tech Stack:** Python dataclass (기존), pyyaml (신규), pytest (테스트)

**Spec:** `docs/superpowers/specs/2026-03-10-config-yaml-externalization-design.md`

---

## 파일 구조

| 파일 | 작업 | 역할 |
|------|------|------|
| `config/config.yaml` | 생성 | 전략 설정값 (주석 포함) |
| `config/settings.py` | 수정 | YAML 로드 + validate() 추가 |
| `requirements.txt` | 수정 | pyyaml 추가 |
| `tests/test_settings.py` | 생성 | YAML 로드, 검증, 폴백 테스트 |

기존 소스/테스트 파일은 **변경 없음**.

---

## Task 1: pyyaml 의존성 추가

**Files:**
- Modify: `requirements.txt:34` (python-dotenv 아래)

- [ ] **Step 1: requirements.txt에 pyyaml 추가**

`requirements.txt`의 `python-dotenv==1.2.2` 아래에 추가:
```
pyyaml==6.0.2
```

- [ ] **Step 2: 설치 확인**

Run: `pip install pyyaml==6.0.2`
Expected: Successfully installed

- [ ] **Step 3: 커밋**

```bash
git add requirements.txt
git commit -m "chore: pyyaml 의존성 추가"
```

---

## Task 2: config.yaml 기본 설정 파일 생성

**Files:**
- Create: `config/config.yaml`

- [ ] **Step 1: config/config.yaml 생성**

```yaml
# ============================================================
# 퀀트 전략 설정 파일
# 이 파일을 수정하여 코드 변경 없이 전략 파라미터를 조정합니다.
# API 키 등 비밀정보는 .env 파일에서 관리합니다.
# ============================================================

# === 팩터 가중치 (합 = 1.0) ===
factor_weights:
  value: 0.40       # 밸류 팩터 비중
  momentum: 0.40    # 모멘텀 팩터 비중
  quality: 0.20     # 퀄리티 팩터 비중

# === 밸류 팩터 세부 가중치 (합 = 1.0) ===
value_weights:
  pbr: 0.50         # 1/PBR 비중
  per: 0.30         # 1/PER 비중
  div: 0.20         # 배당수익률 비중

# === 종목 유니버스 필터 ===
universe:
  market: "KOSPI"                    # KOSPI / KOSDAQ / ALL
  min_market_cap_percentile: 10.0    # 시총 하위 N% 제외
  exclude_finance: true              # 금융주 제외
  min_listing_days: 365              # 최소 상장일수
  min_avg_trading_value: 100000000   # 20일 평균 거래대금 하한 (원)

# === 포트폴리오 ===
portfolio:
  n_stocks: 30                       # 선정 종목 수
  weight_method: "equal"             # equal / value_weighted

# === 거래 비용 & 리스크 관리 ===
trading:
  commission_rate: 0.00015           # 수수료 (0.015%)
  tax_rate: 0.0018                   # 거래세 (0.18%, 매도만)
  slippage: 0.001                    # 슬리피지 (0.1%)
  max_position_pct: 0.10             # 단일 종목 최대 비중 (10%)
  max_turnover_pct: 0.50             # 월간 최대 교체율 (50%)
  max_drawdown_pct: 0.30             # MDD 서킷브레이커 (-30%)
```

- [ ] **Step 2: 커밋**

```bash
git add config/config.yaml
git commit -m "feat: config.yaml 기본 설정 파일 생성"
```

---

## Task 3: YAML 로드 + validate() 구현 (TDD)

**Files:**
- Create: `tests/test_settings.py`
- Modify: `config/settings.py`

### 3-1. YAML 로드 테스트 작성

- [ ] **Step 1: YAML 로드 기본 테스트 작성**

`tests/test_settings.py` 생성:

```python
"""config/settings.py YAML 로드 및 유효성 검사 테스트."""
import os
import tempfile
import pytest
import yaml

from config.settings import (
    Settings,
    FactorWeights,
    ValueWeights,
    UniverseConfig,
    PortfolioConfig,
    TradingConfig,
    _load_yaml,
    _apply_yaml,
    validate_settings,
)


class TestLoadYaml:
    """_load_yaml() 테스트."""

    def test_load_existing_yaml(self, tmp_path):
        """존재하는 YAML 파일을 정상 로드한다."""
        yaml_path = tmp_path / "config.yaml"
        data = {"factor_weights": {"value": 0.50, "momentum": 0.30, "quality": 0.20}}
        yaml_path.write_text(yaml.dump(data), encoding="utf-8")

        result = _load_yaml(str(yaml_path))
        assert result == data

    def test_load_missing_yaml_returns_empty(self, tmp_path):
        """YAML 파일이 없으면 빈 dict를 반환한다."""
        result = _load_yaml(str(tmp_path / "nonexistent.yaml"))
        assert result == {}

    def test_load_empty_yaml_returns_empty(self, tmp_path):
        """빈 YAML 파일은 빈 dict를 반환한다."""
        yaml_path = tmp_path / "empty.yaml"
        yaml_path.write_text("", encoding="utf-8")

        result = _load_yaml(str(yaml_path))
        assert result == {}

    def test_load_invalid_yaml_returns_empty(self, tmp_path):
        """잘못된 YAML 형식이면 빈 dict를 반환한다."""
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text("{{invalid: yaml: [", encoding="utf-8")

        result = _load_yaml(str(yaml_path))
        assert result == {}


class TestApplyYaml:
    """_apply_yaml() 테스트."""

    def test_apply_factor_weights(self):
        """factor_weights 값을 덮어쓴다."""
        s = Settings()
        _apply_yaml(s, {"factor_weights": {"value": 0.50, "momentum": 0.30, "quality": 0.20}})
        assert s.factor_weights.value == 0.50
        assert s.factor_weights.momentum == 0.30
        assert s.factor_weights.quality == 0.20

    def test_apply_partial_factor_weights(self):
        """일부 필드만 지정하면 나머지는 기본값 유지."""
        s = Settings()
        _apply_yaml(s, {"factor_weights": {"value": 0.60}})
        assert s.factor_weights.value == 0.60
        assert s.factor_weights.momentum == 0.40  # 기본값 유지
        assert s.factor_weights.quality == 0.20    # 기본값 유지

    def test_apply_universe_config(self):
        """universe 값을 덮어쓴다."""
        s = Settings()
        _apply_yaml(s, {"universe": {"market": "ALL", "min_listing_days": 180}})
        assert s.universe.market == "ALL"
        assert s.universe.min_listing_days == 180

    def test_apply_trading_config(self):
        """trading 값을 덮어쓴다."""
        s = Settings()
        _apply_yaml(s, {"trading": {"commission_rate": 0.0003, "max_drawdown_pct": 0.20}})
        assert s.trading.commission_rate == 0.0003
        assert s.trading.max_drawdown_pct == 0.20

    def test_apply_portfolio_config(self):
        """portfolio 값을 덮어쓴다."""
        s = Settings()
        _apply_yaml(s, {"portfolio": {"n_stocks": 20}})
        assert s.portfolio.n_stocks == 20

    def test_apply_value_weights(self):
        """value_weights 값을 덮어쓴다."""
        s = Settings()
        _apply_yaml(s, {"value_weights": {"pbr": 0.40, "per": 0.40, "div": 0.20}})
        assert s.value_weights.pbr == 0.40
        assert s.value_weights.per == 0.40

    def test_apply_unknown_section_ignored(self):
        """알 수 없는 섹션은 무시한다."""
        s = Settings()
        _apply_yaml(s, {"unknown_section": {"foo": "bar"}})
        # 에러 없이 통과

    def test_apply_unknown_field_in_section_ignored(self):
        """알 수 없는 필드는 무시한다."""
        s = Settings()
        _apply_yaml(s, {"trading": {"unknown_field": 999}})
        # 에러 없이 통과, 기존 값 유지
        assert s.trading.commission_rate == 0.00015
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `_load_yaml`, `_apply_yaml`, `validate_settings` import 불가

### 3-2. _load_yaml, _apply_yaml 구현

- [ ] **Step 3: config/settings.py에 _load_yaml, _apply_yaml 구현**

`config/settings.py`의 `import` 부분에 추가:
```python
import logging
import yaml

logger = logging.getLogger(__name__)
```

`Settings` 클래스 위에 두 함수 추가:
```python
def _load_yaml(path: str) -> dict:
    """YAML 파일을 로드한다. 없거나 잘못된 경우 빈 dict 반환."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logger.warning("설정 파일이 없습니다: %s (기본값 사용)", path)
        return {}
    except yaml.YAMLError as e:
        logger.error("YAML 파싱 오류: %s (%s) (기본값 사용)", path, e)
        return {}


_YAML_SECTIONS = {
    "factor_weights": FactorWeights,
    "value_weights": ValueWeights,
    "universe": UniverseConfig,
    "portfolio": PortfolioConfig,
    "trading": TradingConfig,
}


def _apply_yaml(settings_obj: "Settings", data: dict) -> None:
    """YAML dict 값을 Settings 객체에 적용한다."""
    for section, cls in _YAML_SECTIONS.items():
        if section not in data:
            continue
        sub = data[section]
        if not isinstance(sub, dict):
            logger.warning("설정 섹션 '%s'이 dict가 아닙니다. 무시합니다.", section)
            continue
        target = getattr(settings_obj, section)
        for key, val in sub.items():
            if hasattr(target, key):
                setattr(target, key, val)
            else:
                logger.warning("알 수 없는 설정: %s.%s (무시)", section, key)
```

- [ ] **Step 4: 테스트 통과 확인 (load/apply)**

Run: `python -m pytest tests/test_settings.py::TestLoadYaml tests/test_settings.py::TestApplyYaml -v`
Expected: 모두 PASS

### 3-3. validate_settings 테스트 작성

- [ ] **Step 5: 유효성 검사 테스트 추가**

`tests/test_settings.py`에 추가:

```python
class TestValidateSettings:
    """validate_settings() 테스트."""

    def test_valid_default_settings(self):
        """기본 설정은 유효성 검사를 통과한다."""
        s = Settings()
        validate_settings(s)  # 에러 없이 통과

    def test_factor_weights_sum_not_one(self):
        """factor_weights 합이 1이 아니면 ValueError."""
        s = Settings()
        s.factor_weights = FactorWeights.__new__(FactorWeights)
        s.factor_weights.value = 0.50
        s.factor_weights.momentum = 0.50
        s.factor_weights.quality = 0.50
        with pytest.raises(ValueError, match="factor_weights 합이 1.0이 아닙니다"):
            validate_settings(s)

    def test_value_weights_sum_not_one(self):
        """value_weights 합이 1이 아니면 ValueError."""
        s = Settings()
        s.value_weights = ValueWeights.__new__(ValueWeights)
        s.value_weights.pbr = 0.80
        s.value_weights.per = 0.30
        s.value_weights.div = 0.20
        with pytest.raises(ValueError, match="value_weights 합이 1.0이 아닙니다"):
            validate_settings(s)

    def test_invalid_market(self):
        """market이 허용값이 아니면 ValueError."""
        s = Settings()
        s.universe.market = "NASDAQ"
        with pytest.raises(ValueError, match="지원하지 않는 market"):
            validate_settings(s)

    def test_invalid_weight_method(self):
        """weight_method가 허용값이 아니면 ValueError."""
        s = Settings()
        s.portfolio.weight_method = "random"
        with pytest.raises(ValueError, match="지원하지 않는 weight_method"):
            validate_settings(s)

    def test_n_stocks_zero(self):
        """n_stocks가 0이면 ValueError."""
        s = Settings()
        s.portfolio.n_stocks = 0
        with pytest.raises(ValueError, match="n_stocks는 1 이상"):
            validate_settings(s)

    def test_negative_commission(self):
        """commission_rate가 음수면 ValueError."""
        s = Settings()
        s.trading.commission_rate = -0.01
        with pytest.raises(ValueError, match="commission_rate.*0~1 범위"):
            validate_settings(s)

    def test_commission_over_one(self):
        """commission_rate가 1 초과면 ValueError."""
        s = Settings()
        s.trading.commission_rate = 1.5
        with pytest.raises(ValueError, match="commission_rate.*0~1 범위"):
            validate_settings(s)

    def test_negative_min_listing_days(self):
        """min_listing_days가 음수면 ValueError."""
        s = Settings()
        s.universe.min_listing_days = -1
        with pytest.raises(ValueError, match="min_listing_days는 0 이상"):
            validate_settings(s)

    def test_min_market_cap_percentile_over_100(self):
        """min_market_cap_percentile이 100 초과면 ValueError."""
        s = Settings()
        s.universe.min_market_cap_percentile = 150.0
        with pytest.raises(ValueError, match="min_market_cap_percentile.*0~100 범위"):
            validate_settings(s)

    def test_negative_min_avg_trading_value(self):
        """min_avg_trading_value가 음수면 ValueError."""
        s = Settings()
        s.universe.min_avg_trading_value = -100
        with pytest.raises(ValueError, match="min_avg_trading_value는 0 이상"):
            validate_settings(s)
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `python -m pytest tests/test_settings.py::TestValidateSettings -v`
Expected: FAIL — `validate_settings` 미구현

### 3-4. validate_settings 구현

- [ ] **Step 7: config/settings.py에 validate_settings 구현**

`_apply_yaml` 아래에 추가:

```python
def validate_settings(s: "Settings") -> None:
    """Settings 객체의 유효성을 검사한다. 실패 시 ValueError."""
    errors: list[str] = []

    # 팩터 가중치 합
    fw_sum = s.factor_weights.value + s.factor_weights.momentum + s.factor_weights.quality
    if abs(fw_sum - 1.0) > 0.001:
        errors.append(f"factor_weights 합이 1.0이 아닙니다: {fw_sum}")

    vw_sum = s.value_weights.pbr + s.value_weights.per + s.value_weights.div
    if abs(vw_sum - 1.0) > 0.001:
        errors.append(f"value_weights 합이 1.0이 아닙니다: {vw_sum}")

    # 비율 필드 범위 (0~1)
    rate_fields = [
        ("commission_rate", s.trading.commission_rate),
        ("tax_rate", s.trading.tax_rate),
        ("slippage", s.trading.slippage),
        ("max_position_pct", s.trading.max_position_pct),
        ("max_turnover_pct", s.trading.max_turnover_pct),
        ("max_drawdown_pct", s.trading.max_drawdown_pct),
    ]
    for name, val in rate_fields:
        if not (0.0 <= val <= 1.0):
            errors.append(f"{name}는 0~1 범위여야 합니다: {val}")

    # 정수 범위
    if s.portfolio.n_stocks < 1:
        errors.append(f"n_stocks는 1 이상이어야 합니다: {s.portfolio.n_stocks}")
    if s.universe.min_listing_days < 0:
        errors.append(f"min_listing_days는 0 이상이어야 합니다: {s.universe.min_listing_days}")
    if s.universe.min_avg_trading_value < 0:
        errors.append(f"min_avg_trading_value는 0 이상이어야 합니다: {s.universe.min_avg_trading_value}")

    # percentile 범위
    if not (0.0 <= s.universe.min_market_cap_percentile <= 100.0):
        errors.append(
            f"min_market_cap_percentile은 0~100 범위여야 합니다: {s.universe.min_market_cap_percentile}"
        )

    # 허용값 검사
    if s.universe.market not in ("KOSPI", "KOSDAQ", "ALL"):
        errors.append(f"지원하지 않는 market: {s.universe.market}")
    if s.portfolio.weight_method not in ("equal", "value_weighted"):
        errors.append(f"지원하지 않는 weight_method: {s.portfolio.weight_method}")

    if errors:
        raise ValueError("\n".join(errors))
```

- [ ] **Step 8: 전체 유효성 검사 테스트 통과 확인**

Run: `python -m pytest tests/test_settings.py::TestValidateSettings -v`
Expected: 모두 PASS

### 3-5. Settings.__post_init__에 YAML 로드 통합

- [ ] **Step 9: Settings에 __post_init__ 통합 테스트 추가**

`tests/test_settings.py`에 추가:

```python
class TestSettingsIntegration:
    """Settings 생성 시 YAML 자동 로드 통합 테스트."""

    def test_settings_loads_yaml_on_init(self, tmp_path, monkeypatch):
        """Settings 생성 시 config.yaml을 자동 로드한다."""
        yaml_path = tmp_path / "config.yaml"
        data = {
            "portfolio": {"n_stocks": 15},
            "universe": {"market": "ALL"},
        }
        yaml_path.write_text(yaml.dump(data), encoding="utf-8")
        monkeypatch.setenv("CONFIG_PATH", str(yaml_path))

        s = Settings()
        assert s.portfolio.n_stocks == 15
        assert s.universe.market == "ALL"

    def test_settings_default_without_yaml(self, tmp_path, monkeypatch):
        """YAML 파일이 없으면 기본값으로 동작한다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "missing.yaml"))
        s = Settings()
        assert s.portfolio.n_stocks == 30
        assert s.universe.market == "KOSPI"

    def test_settings_invalid_yaml_raises(self, tmp_path, monkeypatch):
        """유효하지 않은 설정값이면 ValueError가 발생한다."""
        yaml_path = tmp_path / "config.yaml"
        data = {"portfolio": {"n_stocks": 0}}
        yaml_path.write_text(yaml.dump(data), encoding="utf-8")
        monkeypatch.setenv("CONFIG_PATH", str(yaml_path))

        with pytest.raises(ValueError, match="n_stocks는 1 이상"):
            Settings()
```

- [ ] **Step 10: Settings.__post_init__ 구현**

`config/settings.py`의 `Settings` 클래스에 `__post_init__` 추가:

```python
@dataclass
class Settings:
    # ... 기존 필드 그대로 ...

    def __post_init__(self) -> None:
        config_path = os.getenv("CONFIG_PATH", "config/config.yaml")
        data = _load_yaml(config_path)
        if data:
            _apply_yaml(self, data)
        validate_settings(self)
```

기존 `FactorWeights.__post_init__`의 가중치 합 검사는 `validate_settings`에서 처리하므로 제거:

```python
@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20
```

- [ ] **Step 11: 전체 테스트 통과 확인**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 모두 PASS

- [ ] **Step 12: 커밋**

```bash
git add config/settings.py tests/test_settings.py
git commit -m "feat: YAML 설정 로드 + 유효성 검사 구현"
```

---

## Task 4: 기존 테스트 회귀 확인

- [ ] **Step 1: 전체 테스트 스위트 실행**

Run: `python -m pytest tests/ -v`
Expected: 기존 179개 + 신규 테스트 모두 PASS

`Settings()`가 이제 `config/config.yaml`을 로드하므로, 기존 테스트에서 mock이 settings를 통째로 교체하는 경우 문제없는지 확인. 기존 테스트들은 `@patch("모듈.settings")` 패턴으로 mock하므로 `__post_init__` 타이밍과 무관.

만약 실패하는 테스트가 있으면:
- `CONFIG_PATH`를 존재하지 않는 경로로 설정하는 conftest fixture 추가 검토

- [ ] **Step 2: 실패 시 conftest.py에 fixture 추가**

필요한 경우에만 `tests/conftest.py`에 추가:
```python
@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """테스트 시 config.yaml 로드를 비활성화."""
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
```

- [ ] **Step 3: 커밋 (변경 있는 경우)**

```bash
git add tests/conftest.py
git commit -m "test: 테스트 격리를 위한 CONFIG_PATH fixture 추가"
```

---

## Task 5: 최종 검증 및 정리

- [ ] **Step 1: config.yaml 값 변경 후 동작 확인 (수동)**

`config/config.yaml`에서 `n_stocks: 15`로 변경 후:
```python
python -c "from config.settings import settings; print(settings.portfolio.n_stocks)"
```
Expected: `15`

- [ ] **Step 2: 잘못된 값 에러 메시지 확인 (수동)**

`config/config.yaml`에서 `n_stocks: 0`으로 변경 후:
```python
python -c "from config.settings import settings"
```
Expected: `ValueError: n_stocks는 1 이상이어야 합니다: 0`

- [ ] **Step 3: config.yaml 기본값 복원**

변경했던 값을 원래대로 복원 (`n_stocks: 30`).

- [ ] **Step 4: 최종 전체 테스트**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS
