# config/settings.py
from dataclasses import dataclass, field, fields
import logging
import os

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 금액 프리셋이 덮어쓸 수 없는 전략 전용 키
STRATEGY_ONLY_KEYS = {
    "factor_weights", "value_weights", "momentum", "quality",
    "volatility", "market_regime",
}
# trading 섹션 내 전략 전용 키
STRATEGY_ONLY_TRADING_KEYS = {
    "max_drawdown_pct", "vol_target", "trailing_stop_pct", "max_turnover_pct",
}


@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20


@dataclass
class ValueWeights:
    """밸류 팩터 내 세부 지표 가중치"""

    pbr: float = 0.50
    pcr: float = 0.30
    div: float = 0.20


@dataclass
class UniverseConfig:
    market: str = "KOSPI"  # "KOSPI", "KOSDAQ", "ALL" (KOSPI+KOSDAQ)
    min_market_cap_percentile: float = 10.0  # 시가총액 하위 10% 제외
    exclude_finance: bool = True  # 금융주 제외
    min_listing_days: int = 365  # 상장 1년 미만 제외
    min_avg_trading_value: int = 100_000_000  # 20일 평균 거래대금 하한 (1억원)


@dataclass
class MomentumConfig:
    """모멘텀 팩터 설정"""

    absolute_momentum_enabled: bool = True  # 듀얼 모멘텀 활성화
    risk_free_rate: float = 0.035  # 연간 무위험 수익률 (국고채 3년 기준 3.5%)


@dataclass
class QualityConfig:
    """퀄리티 팩터 설정"""

    fscore_enabled: bool = True  # F-Score 필터 활성화
    min_fscore: int = 2  # 최소 F-Score (5점 만점, 2점 이상만 통과 — 최악만 제거)


@dataclass
class VolatilityConfig:
    """변동성 필터 설정"""

    filter_enabled: bool = True  # 변동성 필터 활성화
    lookback_days: int = 252  # 변동성 계산 기간 (영업일)
    max_percentile: float = 80.0  # 상위 N% 이상 변동성 종목 제외 (80 = 상위 20% 제외)


@dataclass
class MarketRegimeConfig:
    """시장 레짐 필터 설정 (하락장 방어)"""

    enabled: bool = True  # 시장 레짐 필터 활성화
    ma_days: int = 200  # 이동평균 기간 (기본 200일)
    partial_ratio: float = 0.5  # 중립 시장 투자 비중 (50%)
    defensive_ratio: float = 0.3  # 약세 시장 투자 비중 (30%)


@dataclass
class PortfolioConfig:
    n_stocks: int = 30
    weight_method: str = "equal"  # equal / value_weighted
    initial_cash: int = 10_000_000  # 백테스트 초기 자금 (기본 1000만원)
    max_investment_amount: int = 0  # 최대 투자 금액 (0=전액 투자, 양수=고정 금액)
    rebalance_frequency: str = "quarterly"  # monthly / quarterly
    holding_buffer_ratio: float = 1.5  # 종목 교체 버퍼 (n_stocks × ratio 이내면 유지)


@dataclass
class TradingConfig:
    commission_rate: float = 0.00015  # 수수료 0.015%
    tax_rate: float = 0.0015  # 거래세 0.15% (매도만, 2025년 기준)
    slippage: float = 0.001  # 슬리피지 0.1%
    max_position_pct: float = 0.10  # 단일 종목 최대 비중 10%
    max_turnover_pct: float = 0.50  # 월간 최대 교체율 50%
    max_drawdown_pct: Optional[float] = 0.25  # MDD 서킷브레이커 (None=비활성화)
    trailing_stop_pct: Optional[float] = 0.20  # 종목별 트레일링 스톱 (None=비활성화)
    vol_target: Optional[float] = 0.15  # 변동성 타겟팅 (None=비활성화)
    vol_lookback_days: int = 60  # 변동성 계산 기간 (거래일)


@dataclass
class RiskGuardConfig:
    """리스크 감시 설정"""

    enabled: bool = True
    stop_loss_pct: float = -20.0  # 종목별 손절 경고 기준 (%)
    max_drawdown_alert_pct: float = -15.0  # 포트폴리오 드로다운 경고 기준 (%)
    check_interval_minutes: int = 30  # 장중 체크 간격 (분)


@dataclass
class MonitoringConfig:
    """모니터링 설정"""

    snapshot_enabled: bool = True  # 일간 스냅샷 DB 저장
    benchmark_enabled: bool = True  # KOSPI 벤치마크 비교
    risk_guard: RiskGuardConfig = field(default_factory=RiskGuardConfig)


# --- YAML 로드 / 적용 / 검증 ---


def _load_yaml(path: str) -> dict:
    """YAML 파일을 로드한다. 없거나 잘못된 경우 빈 dict 반환."""
    if yaml is None:
        logger.warning("pyyaml 미설치 — pip install pyyaml 실행 필요 (기본값 사용)")
        return {}
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
    "momentum": MomentumConfig,
    "quality": QualityConfig,
    "volatility": VolatilityConfig,
    "market_regime": MarketRegimeConfig,
    "universe": UniverseConfig,
    "portfolio": PortfolioConfig,
    "trading": TradingConfig,
    "monitoring": MonitoringConfig,
}


def _apply_dict_to_dataclass(target: object, data: dict, prefix: str = "") -> None:
    """dict 값을 dataclass 인스턴스에 적용한다 (중첩 dataclass 지원)."""
    for key, val in data.items():
        if not hasattr(target, key):
            logger.warning("알 수 없는 설정: %s%s (무시)", prefix, key)
            continue
        current = getattr(target, key)
        if isinstance(val, dict) and hasattr(current, "__dataclass_fields__"):
            _apply_dict_to_dataclass(current, val, prefix=f"{prefix}{key}.")
        else:
            setattr(target, key, val)


def _apply_section_data(settings_obj: "Settings", data: dict) -> None:
    """YAML dict의 섹션별 값을 Settings 객체에 적용한다."""
    for section in _YAML_SECTIONS:
        if section not in data:
            continue
        sub = data[section]
        if not isinstance(sub, dict):
            logger.warning("설정 섹션 '%s'이 dict가 아닙니다. 무시합니다.", section)
            continue
        target = getattr(settings_obj, section)
        _apply_dict_to_dataclass(target, sub, prefix=f"{section}.")


def _apply_yaml(settings_obj: "Settings", data: dict) -> None:
    """YAML dict 값을 Settings 객체에 적용한다.

    적용 순서: 전략 프리셋 → 금액 프리셋 → 개별 설정 덮어쓰기
    """
    presets = data.get("presets", {})
    preset_name = data.get("preset")
    sizing_name = data.get("sizing")

    # 1단계: 전략 프리셋 적용
    if preset_name and presets:
        if preset_name in presets:
            logger.info("전략 프리셋 적용: %s", preset_name)
            _apply_section_data(settings_obj, presets[preset_name])
        else:
            logger.warning("존재하지 않는 전략 프리셋: %s (무시)", preset_name)

    # 2단계: 금액 프리셋 적용 (전략 전용 키 충돌 감지)
    if sizing_name and presets:
        if sizing_name in presets:
            sizing_data = dict(presets[sizing_name])  # 원본 보존

            # 전략 전용 섹션 키 충돌 검사
            for key in list(sizing_data.keys()):
                if key in STRATEGY_ONLY_KEYS:
                    logger.warning(
                        "금액 프리셋 '%s'이 전략 전용 키 '%s'를 포함합니다. 무시합니다.",
                        sizing_name, key,
                    )
                    del sizing_data[key]

            # trading 내 전략 전용 키 충돌 검사
            if "trading" in sizing_data and isinstance(sizing_data["trading"], dict):
                trading = sizing_data["trading"]
                for key in list(trading.keys()):
                    if key in STRATEGY_ONLY_TRADING_KEYS:
                        logger.warning(
                            "금액 프리셋 '%s'의 trading.%s는 전략 전용 키입니다. 무시합니다.",
                            sizing_name, key,
                        )
                        del trading[key]

            logger.info("금액 프리셋 적용: %s", sizing_name)
            _apply_section_data(settings_obj, sizing_data)
        else:
            logger.warning("존재하지 않는 금액 프리셋: %s (무시)", sizing_name)

    # 3단계: 개별 설정 덮어쓰기 (preset, sizing, presets 키 제외)
    individual = {k: v for k, v in data.items() if k not in ("preset", "sizing", "presets")}
    if individual:
        _apply_section_data(settings_obj, individual)


def validate_settings(s: "Settings") -> None:
    """Settings 객체의 유효성을 검사한다. 실패 시 ValueError."""
    errors: list[str] = []

    # 팩터 가중치 합
    fw_sum = s.factor_weights.value + s.factor_weights.momentum + s.factor_weights.quality
    if abs(fw_sum - 1.0) > 0.001:
        errors.append(f"factor_weights 합이 1.0이 아닙니다: {fw_sum}")

    vw_sum = s.value_weights.pbr + s.value_weights.pcr + s.value_weights.div
    if abs(vw_sum - 1.0) > 0.001:
        errors.append(f"value_weights 합이 1.0이 아닙니다: {vw_sum}")

    # 비율 필드 범위 (0~1, None 허용 필드는 별도 처리)
    rate_fields: list[tuple[str, float]] = [
        ("commission_rate", s.trading.commission_rate),
        ("tax_rate", s.trading.tax_rate),
        ("slippage", s.trading.slippage),
        ("max_position_pct", s.trading.max_position_pct),
        ("max_turnover_pct", s.trading.max_turnover_pct),
    ]
    for name, val in rate_fields:
        if not (0.0 <= val <= 1.0):
            errors.append(f"{name}는 0~1 범위여야 합니다: {val}")

    # None 허용 필드 (None = 비활성화)
    for name, val in [
        ("max_drawdown_pct", s.trading.max_drawdown_pct),
        ("vol_target", s.trading.vol_target),
        ("trailing_stop_pct", s.trading.trailing_stop_pct),
    ]:
        if val is not None:
            if not (0.0 <= val <= 1.0):
                errors.append(f"{name}는 0~1 범위여야 합니다: {val}")
            if abs(val - 0.99) < 0.001:
                logger.warning(
                    "%s=0.99는 사실상 비활성화입니다. null을 사용하세요.", name
                )

    # 정수 범위
    if s.portfolio.n_stocks < 1:
        errors.append(f"n_stocks는 1 이상이어야 합니다: {s.portfolio.n_stocks}")
    if s.portfolio.max_investment_amount < 0:
        errors.append(
            f"max_investment_amount는 0 이상이어야 합니다: {s.portfolio.max_investment_amount}"
        )
    if s.universe.min_listing_days < 0:
        errors.append(f"min_listing_days는 0 이상이어야 합니다: {s.universe.min_listing_days}")
    if s.universe.min_avg_trading_value < 0:
        errors.append(
            f"min_avg_trading_value는 0 이상이어야 합니다: {s.universe.min_avg_trading_value}"
        )

    # percentile 범위
    if not (0.0 <= s.universe.min_market_cap_percentile <= 100.0):
        errors.append(
            f"min_market_cap_percentile은 0~100 범위여야 합니다: "
            f"{s.universe.min_market_cap_percentile}"
        )

    # 허용값 검사
    if s.universe.market not in ("KOSPI", "KOSDAQ", "ALL"):
        errors.append(f"지원하지 않는 market: {s.universe.market}")
    if s.portfolio.weight_method not in ("equal", "value_weighted"):
        errors.append(f"지원하지 않는 weight_method: {s.portfolio.weight_method}")
    if s.portfolio.rebalance_frequency not in ("monthly", "quarterly"):
        errors.append(f"지원하지 않는 rebalance_frequency: {s.portfolio.rebalance_frequency}")
    if s.portfolio.holding_buffer_ratio < 1.0:
        errors.append(f"holding_buffer_ratio는 1.0 이상이어야 합니다: {s.portfolio.holding_buffer_ratio}")

    if errors:
        raise ValueError("\n".join(errors))


@dataclass
class Settings:
    factor_weights: FactorWeights = field(default_factory=FactorWeights)
    value_weights: ValueWeights = field(default_factory=ValueWeights)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    volatility: VolatilityConfig = field(default_factory=VolatilityConfig)
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    # 키움 REST API
    kiwoom_app_key: str = field(default_factory=lambda: os.getenv("KIWOOM_APP_KEY", ""))
    kiwoom_app_secret: str = field(
        default_factory=lambda: os.getenv("KIWOOM_APP_SECRET", "")
    )
    kiwoom_account_no: str = field(
        default_factory=lambda: os.getenv("KIWOOM_ACCOUNT_NO", "")
    )
    is_paper_trading: bool = field(
        default_factory=lambda: os.getenv("IS_PAPER_TRADING", "true").strip().lower()
        not in ("false", "0", "no")
    )

    # 텔레그램
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )

    # KRX Open API
    krx_openapi_key: str = field(
        default_factory=lambda: os.getenv("KRX_OPENAPI_KEY", "")
    )

    # DART OpenAPI
    dart_api_key: str = field(
        default_factory=lambda: os.getenv("DART_API_KEY", "")
    )

    # 내부 경로
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/quant.db"))
    log_path: str = field(
        default_factory=lambda: os.getenv("LOG_PATH", "logs/quant.log")
    )

    def __post_init__(self) -> None:
        config_path = os.getenv("CONFIG_PATH", "config/config.yaml")
        data = _load_yaml(config_path)
        if data:
            _apply_yaml(self, data)
        validate_settings(self)


# 전역 싱글톤
settings = Settings()
