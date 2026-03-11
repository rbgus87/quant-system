# config/settings.py
from dataclasses import dataclass, field
import logging
import os

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20


@dataclass
class ValueWeights:
    """밸류 팩터 내 세부 지표 가중치"""

    pbr: float = 0.50
    per: float = 0.30
    div: float = 0.20


@dataclass
class UniverseConfig:
    market: str = "KOSPI"  # "KOSPI", "KOSDAQ", "ALL" (KOSPI+KOSDAQ)
    min_market_cap_percentile: float = 10.0  # 시가총액 하위 10% 제외
    exclude_finance: bool = True  # 금융주 제외
    min_listing_days: int = 365  # 상장 1년 미만 제외
    min_avg_trading_value: int = 100_000_000  # 20일 평균 거래대금 하한 (1억원)


@dataclass
class PortfolioConfig:
    n_stocks: int = 30
    weight_method: str = "equal"  # equal / value_weighted
    initial_cash: int = 10_000_000  # 백테스트 초기 자금 (기본 1000만원)
    max_investment_amount: int = 0  # 최대 투자 금액 (0=전액 투자, 양수=고정 금액)


@dataclass
class TradingConfig:
    commission_rate: float = 0.00015  # 수수료 0.015%
    tax_rate: float = 0.0018  # 거래세 0.18% (매도만)
    slippage: float = 0.001  # 슬리피지 0.1%
    max_position_pct: float = 0.10  # 단일 종목 최대 비중 10%
    max_turnover_pct: float = 0.50  # 월간 최대 교체율 50%
    max_drawdown_pct: float = 0.30  # MDD 서킷 브레이커 (-30% 이하 시 리밸런싱 중단)


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

    if errors:
        raise ValueError("\n".join(errors))


@dataclass
class Settings:
    factor_weights: FactorWeights = field(default_factory=FactorWeights)
    value_weights: ValueWeights = field(default_factory=ValueWeights)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)

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
