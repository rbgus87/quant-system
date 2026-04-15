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

    fscore_enabled: bool = True  # F-Score 필터 활성화 (확정)
    min_fscore: int = 2  # 권고: 4 (config.yaml 프리셋에서 설정). 5는 CAGR -12.7%p 부작용
    # ── 실험용 옵션 (모두 기본 False — docs/POLICY.md "검토했으나 미채택" 참조) ──
    # strict_reporting_lag: True 시 재무 팩터를 전년도 연간 보고서로만 계산.
    # 2026-04-15 평가: CAGR -12.18%p (lag_impact_analysis.md). 코드는 학습용으로 유지.
    strict_reporting_lag: bool = False
    # eps_flip_filter: 최근 N개월 EPS 부호 반전 + 변동률 임계 초과 종목 배제.
    # 005620 회피 가능하나 정상 턴어라운드 종목까지 99개 배제 → CAGR -7.08%p.
    eps_flip_filter_enabled: bool = False
    eps_flip_lookback_months: int = 4
    eps_flip_min_change_pct: float = 1.5  # 150%
    # halt_history_filter: 최근 N일 내 거래정지(volume=0) 일수 임계 초과 종목 배제.
    # 005620은 기준일에 정지 이력 부족 → 회피 실패. CAGR 영향 -0.06%p (효과 없음).
    halt_history_filter_enabled: bool = False
    halt_history_lookback_days: int = 60
    halt_history_max_halt_days: int = 5


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
    rebalance_time: str = "08:50"  # 리밸런싱 체크 시각 (HH:MM, 장 시작 전 권장)
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


# ──────────────────────────────────────────────
# DART 공시 알림
# ──────────────────────────────────────────────

# 카테고리 별칭 → pblntf_detail_ty 코드 매핑
DART_CATEGORY_ALIASES: dict[str, list[str]] = {
    "major_report": ["B001", "B002"],
    "unfaithful_disclosure": ["E001"],
    "fair_disclosure": ["E002"],
    "largest_shareholder": ["B003"],
    "convertible_bond": ["G001", "G002"],
    "capital_change": ["G003", "G004"],
    "merger_split": ["H001", "H002", "H003"],
    "stock_exchange": ["I001", "I002"],
    "annual_report": ["A001"],
    "semi_annual_report": ["A002"],
    "quarterly_report": ["A003"],
}


def resolve_dart_categories(categories: list[str]) -> list[str]:
    """카테고리 별칭 리스트를 pblntf_detail_ty 코드 리스트로 변환한다.

    별칭이면 매핑된 코드들로 확장, 이미 코드(예: B001)면 그대로 유지.

    Args:
        categories: 별칭 또는 코드 리스트

    Returns:
        pblntf_detail_ty 코드 리스트 (중복 제거)

    Raises:
        ValueError: 인식할 수 없는 카테고리가 있을 때
    """
    codes: list[str] = []
    unknown: list[str] = []
    for cat in categories:
        if cat in DART_CATEGORY_ALIASES:
            codes.extend(DART_CATEGORY_ALIASES[cat])
        elif len(cat) == 4 and cat[0].isalpha() and cat[1:].isdigit():
            codes.append(cat)
        else:
            unknown.append(cat)

    if unknown:
        valid = sorted(DART_CATEGORY_ALIASES.keys())
        raise ValueError(
            f"알 수 없는 dart_notifier 카테고리: {unknown}\n"
            f"유효한 별칭: {valid}\n"
            f"또는 DART pblntf_detail_ty 코드(예: B001)를 직접 사용하세요."
        )

    # 중복 제거 (순서 유지)
    seen: set[str] = set()
    result: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


@dataclass
class InstantAlertConfig:
    """즉시 알림 설정"""

    enabled: bool = True
    categories: list[str] = field(default_factory=lambda: [
        "major_report", "unfaithful_disclosure", "fair_disclosure",
        "largest_shareholder", "convertible_bond", "capital_change",
        "merger_split",
    ])


@dataclass
class DailySummaryConfig:
    """일일 요약 설정"""

    enabled: bool = True
    send_time: str = "17:00"


@dataclass
class ApiLimitConfig:
    """API 한도 설정"""

    daily_warning_threshold: int = 8000


@dataclass
class DartNotifierConfig:
    """DART 공시 알림 설정 (top-level 섹션)"""

    enabled: bool = True
    polling_interval_minutes: int = 5
    market_hours_only: bool = True
    market_open: str = "09:00"
    market_close: str = "15:30"
    instant_alert: InstantAlertConfig = field(default_factory=InstantAlertConfig)
    daily_summary: DailySummaryConfig = field(default_factory=DailySummaryConfig)
    api_limit: ApiLimitConfig = field(default_factory=ApiLimitConfig)

    def get_instant_codes(self) -> list[str]:
        """즉시 알림 카테고리를 pblntf_detail_ty 코드 리스트로 변환한다."""
        return resolve_dart_categories(self.instant_alert.categories)


# ──────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────


@dataclass
class LoggingConfig:
    """로깅 설정"""

    trading_log_retention_days: int = 90
    system_log_retention_days: int = 30


# ──────────────────────────────────────────────
# 스케줄러 설정
# ──────────────────────────────────────────────


@dataclass
class DailyDataCollectionConfig:
    """일별 데이터 수집 Job 설정 (기본 16:30 — KRX 당일 업데이트 지연 대응)"""

    enabled: bool = True
    hour: int = 16
    minute: int = 30
    markets: list[str] = field(default_factory=lambda: ["KOSPI"])


@dataclass
class DelistedRefreshConfig:
    """상장폐지 데이터 월간 갱신 Job 설정.

    day_of_month=-1 → 마지막 영업일 (APScheduler 'last' 트리거)
    auto_download=False → KIND 자동 다운로드 실패 시 텔레그램 수동 안내만 발송
    """

    enabled: bool = True
    day_of_month: int = -1  # -1 = last business day
    hour: int = 16
    minute: int = 0
    auto_download: bool = False


@dataclass
class ScheduleConfig:
    """스케줄러 Job 설정"""

    daily_data_collection: DailyDataCollectionConfig = field(
        default_factory=DailyDataCollectionConfig
    )
    delisted_refresh: DelistedRefreshConfig = field(
        default_factory=DelistedRefreshConfig
    )


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
    "dart_notifier": DartNotifierConfig,
    "logging": LoggingConfig,
    "schedule": ScheduleConfig,
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
    try:
        from datetime import datetime as _dt
        _dt.strptime(s.portfolio.rebalance_time, "%H:%M")
    except ValueError:
        errors.append(
            f"portfolio.rebalance_time 형식 오류. HH:MM 형식이어야 합니다. "
            f"(현재: {s.portfolio.rebalance_time})"
        )
    if s.portfolio.holding_buffer_ratio < 1.0:
        errors.append(f"holding_buffer_ratio는 1.0 이상이어야 합니다: {s.portfolio.holding_buffer_ratio}")

    # ── dart_notifier 검증 ──
    dn = s.dart_notifier
    if dn.enabled:
        # market_open < market_close
        try:
            from datetime import datetime as _dt

            t_open = _dt.strptime(dn.market_open, "%H:%M")
            t_close = _dt.strptime(dn.market_close, "%H:%M")
            if t_open >= t_close:
                errors.append(
                    f"dart_notifier.market_open({dn.market_open})이 "
                    f"market_close({dn.market_close})보다 같거나 늦습니다."
                )
        except ValueError:
            errors.append(
                f"dart_notifier.market_open/market_close 형식 오류. "
                f"HH:MM 형식이어야 합니다. (현재: {dn.market_open}, {dn.market_close})"
            )

        # 카테고리 별칭 검증
        try:
            resolve_dart_categories(dn.instant_alert.categories)
        except ValueError as e:
            errors.append(str(e))

        # polling_interval_minutes 범위
        if dn.polling_interval_minutes < 1:
            errors.append(
                f"dart_notifier.polling_interval_minutes는 1 이상이어야 합니다: "
                f"{dn.polling_interval_minutes}"
            )

        # daily_summary.send_time 형식
        try:
            _dt.strptime(dn.daily_summary.send_time, "%H:%M")
        except ValueError:
            errors.append(
                f"dart_notifier.daily_summary.send_time 형식 오류. "
                f"HH:MM 형식이어야 합니다. (현재: {dn.daily_summary.send_time})"
            )

        # api_limit 범위
        if dn.api_limit.daily_warning_threshold < 100:
            errors.append(
                f"dart_notifier.api_limit.daily_warning_threshold는 100 이상이어야 합니다: "
                f"{dn.api_limit.daily_warning_threshold}"
            )

    # ── schedule 검증 ──
    dc = s.schedule.daily_data_collection
    if not (0 <= dc.hour <= 23):
        errors.append(
            f"schedule.daily_data_collection.hour는 0~23이어야 합니다: {dc.hour}"
        )
    if not (0 <= dc.minute <= 59):
        errors.append(
            f"schedule.daily_data_collection.minute는 0~59이어야 합니다: {dc.minute}"
        )
    if not dc.markets:
        errors.append("schedule.daily_data_collection.markets가 비어 있습니다")
    for m in dc.markets:
        if m not in ("KOSPI", "KOSDAQ"):
            errors.append(
                f"schedule.daily_data_collection.markets에 지원하지 않는 값: {m}"
            )

    dr = s.schedule.delisted_refresh
    if not (0 <= dr.hour <= 23):
        errors.append(f"schedule.delisted_refresh.hour는 0~23이어야 합니다: {dr.hour}")
    if not (0 <= dr.minute <= 59):
        errors.append(
            f"schedule.delisted_refresh.minute는 0~59이어야 합니다: {dr.minute}"
        )
    if not (dr.day_of_month == -1 or 1 <= dr.day_of_month <= 28):
        errors.append(
            f"schedule.delisted_refresh.day_of_month는 -1(마지막 영업일) 또는 "
            f"1~28이어야 합니다: {dr.day_of_month}"
        )

    # ── logging 검증 ──
    lg = s.logging
    if lg.trading_log_retention_days < 1:
        errors.append(
            f"logging.trading_log_retention_days는 1 이상이어야 합니다: "
            f"{lg.trading_log_retention_days}"
        )
    if lg.system_log_retention_days < 1:
        errors.append(
            f"logging.system_log_retention_days는 1 이상이어야 합니다: "
            f"{lg.system_log_retention_days}"
        )

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
    dart_notifier: DartNotifierConfig = field(default_factory=DartNotifierConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

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
            # 기본값 폴백 경고
            if "dart_notifier" not in data:
                logger.warning(
                    "config.yaml에 dart_notifier 섹션 없음 — 기본값으로 동작합니다. "
                    "docs/config_reference.md를 참조하세요."
                )
            if "logging" not in data:
                logger.warning(
                    "config.yaml에 logging 섹션 없음 — 기본값으로 동작합니다. "
                    "docs/config_reference.md를 참조하세요."
                )
        validate_settings(self)


# 전역 싱글톤
settings = Settings()
