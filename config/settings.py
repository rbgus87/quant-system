# config/settings.py
from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20

    def __post_init__(self):
        total = self.value + self.momentum + self.quality
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"팩터 가중치 합이 1이 아닙니다: {total}")


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


@dataclass
class TradingConfig:
    commission_rate: float = 0.00015  # 수수료 0.015%
    tax_rate: float = 0.0018  # 거래세 0.18% (매도만)
    slippage: float = 0.001  # 슬리피지 0.1%
    max_position_pct: float = 0.10  # 단일 종목 최대 비중 10%
    max_turnover_pct: float = 0.50  # 월간 최대 교체율 50%
    max_drawdown_pct: float = 0.30  # MDD 서킷 브레이커 (-30% 이하 시 리밸런싱 중단)


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


# 전역 싱글톤
settings = Settings()
