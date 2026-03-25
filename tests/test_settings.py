"""config/settings.py YAML 로드 및 유효성 검사 테스트."""
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

    def test_apply_factor_weights(self, monkeypatch, tmp_path):
        """factor_weights 값을 덮어쓴다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"factor_weights": {"value": 0.50, "momentum": 0.30, "quality": 0.20}})
        assert s.factor_weights.value == 0.50
        assert s.factor_weights.momentum == 0.30
        assert s.factor_weights.quality == 0.20

    def test_apply_partial_factor_weights(self, monkeypatch, tmp_path):
        """일부 필드만 지정하면 나머지는 기본값 유지."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"factor_weights": {"value": 0.60}})
        assert s.factor_weights.value == 0.60
        assert s.factor_weights.momentum == 0.40
        assert s.factor_weights.quality == 0.20

    def test_apply_universe_config(self, monkeypatch, tmp_path):
        """universe 값을 덮어쓴다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"universe": {"market": "ALL", "min_listing_days": 180}})
        assert s.universe.market == "ALL"
        assert s.universe.min_listing_days == 180

    def test_apply_trading_config(self, monkeypatch, tmp_path):
        """trading 값을 덮어쓴다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"trading": {"commission_rate": 0.0003, "max_drawdown_pct": 0.20}})
        assert s.trading.commission_rate == 0.0003
        assert s.trading.max_drawdown_pct == 0.20

    def test_apply_portfolio_config(self, monkeypatch, tmp_path):
        """portfolio 값을 덮어쓴다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"portfolio": {"n_stocks": 20}})
        assert s.portfolio.n_stocks == 20

    def test_apply_value_weights(self, monkeypatch, tmp_path):
        """value_weights 값을 덮어쓴다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"value_weights": {"pbr": 0.40, "pcr": 0.40, "div": 0.20}})
        assert s.value_weights.pbr == 0.40
        assert s.value_weights.pcr == 0.40

    def test_apply_unknown_section_ignored(self, monkeypatch, tmp_path):
        """알 수 없는 섹션은 무시한다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"unknown_section": {"foo": "bar"}})

    def test_apply_unknown_field_in_section_ignored(self, monkeypatch, tmp_path):
        """알 수 없는 필드는 무시한다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        _apply_yaml(s, {"trading": {"unknown_field": 999}})
        assert s.trading.commission_rate == 0.00015


class TestValidateSettings:
    """validate_settings() 테스트."""

    def test_valid_default_settings(self, monkeypatch, tmp_path):
        """기본 설정은 유효성 검사를 통과한다."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        validate_settings(s)

    def test_factor_weights_sum_not_one(self, monkeypatch, tmp_path):
        """factor_weights 합이 1이 아니면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.factor_weights.value = 0.50
        s.factor_weights.momentum = 0.50
        s.factor_weights.quality = 0.50
        with pytest.raises(ValueError, match="factor_weights 합이 1.0이 아닙니다"):
            validate_settings(s)

    def test_value_weights_sum_not_one(self, monkeypatch, tmp_path):
        """value_weights 합이 1이 아니면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.value_weights.pbr = 0.80
        s.value_weights.pcr = 0.30
        s.value_weights.div = 0.20
        with pytest.raises(ValueError, match="value_weights 합이 1.0이 아닙니다"):
            validate_settings(s)

    def test_invalid_market(self, monkeypatch, tmp_path):
        """market이 허용값이 아니면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.universe.market = "NASDAQ"
        with pytest.raises(ValueError, match="지원하지 않는 market"):
            validate_settings(s)

    def test_invalid_weight_method(self, monkeypatch, tmp_path):
        """weight_method가 허용값이 아니면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.portfolio.weight_method = "random"
        with pytest.raises(ValueError, match="지원하지 않는 weight_method"):
            validate_settings(s)

    def test_n_stocks_zero(self, monkeypatch, tmp_path):
        """n_stocks가 0이면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.portfolio.n_stocks = 0
        with pytest.raises(ValueError, match="n_stocks는 1 이상"):
            validate_settings(s)

    def test_negative_commission(self, monkeypatch, tmp_path):
        """commission_rate가 음수면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.trading.commission_rate = -0.01
        with pytest.raises(ValueError, match="commission_rate.*0~1 범위"):
            validate_settings(s)

    def test_commission_over_one(self, monkeypatch, tmp_path):
        """commission_rate가 1 초과면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.trading.commission_rate = 1.5
        with pytest.raises(ValueError, match="commission_rate.*0~1 범위"):
            validate_settings(s)

    def test_negative_min_listing_days(self, monkeypatch, tmp_path):
        """min_listing_days가 음수면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.universe.min_listing_days = -1
        with pytest.raises(ValueError, match="min_listing_days는 0 이상"):
            validate_settings(s)

    def test_min_market_cap_percentile_over_100(self, monkeypatch, tmp_path):
        """min_market_cap_percentile이 100 초과면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.universe.min_market_cap_percentile = 150.0
        with pytest.raises(ValueError, match="min_market_cap_percentile.*0~100 범위"):
            validate_settings(s)

    def test_negative_min_avg_trading_value(self, monkeypatch, tmp_path):
        """min_avg_trading_value가 음수면 ValueError."""
        monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "x.yaml"))
        s = Settings()
        s.universe.min_avg_trading_value = -100
        with pytest.raises(ValueError, match="min_avg_trading_value는 0 이상"):
            validate_settings(s)


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
