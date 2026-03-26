# 02. 시스템 아키텍처 & 공통 설정

## 2-1. 전체 데이터 흐름

```
[pykrx / KRX]
     │ OHLCV, PBR, PER, EPS, BPS, DIV, 시가총액
     ▼
[data/collector.py]  →  [data/storage.py]  →  [SQLite DB]
     │
     ▼
[factors/]
  value.py     → 밸류 스코어 (0~100)
  momentum.py  → 모멘텀 스코어 (0~100)
  quality.py   → 퀄리티 스코어 (OP/A + EY + F-Score, v2.0: 필터 전용)
     │
     ▼
[factors/composite.py]
  → 복합 스코어 = V×0.7 + M×0.3 + Q×0.0 (v2.0)
  → 유니버스 필터 적용
  → 상위 30개 종목 선정
     │
     ├──▶ [backtest/engine.py]   백테스트 시뮬레이션
     │         │
     │         └──▶ [backtest/metrics.py + report.py]  성과 분석
     │
     └──▶ [strategy/rebalancer.py]
               │
               ▼
          [trading/order.py]
               │
               ▼
          [trading/kiwoom_api.py]  → api.kiwoom.com / mockapi.kiwoom.com
               │
               ▼
          [notify/telegram.py]    → 리밸런싱 결과 알림
               │
          [dashboard/app.py]      → Streamlit 모니터링
          [gui/]                  → PyQt6 데스크탑 GUI

[scheduler/main.py]  → 분기/월말 자동 실행 트리거 (APScheduler)
```

---

## 2-2. 프로젝트 구조

```
korean-quant/
├── CLAUDE.md                   ← Claude Code 컨텍스트 (필수)
├── PRD.md                      ← 전략 명세
├── docs/                       ← 개발 가이드 문서
├── .env                        ← 환경 변수 (git 제외)
├── .gitignore
├── requirements.txt
│
├── config/
│   ├── __init__.py
│   ├── settings.py             ← 전역 설정 싱글톤 (YAML 오버라이드 지원)
│   ├── config.yaml             ← 전략 프리셋 외부 설정
│   ├── calendar.py             ← KRX 영업일 캘린더
│   └── logging_config.py       ← 로깅 설정
│
├── data/
│   ├── __init__.py
│   ├── collector.py            ← 멀티소스 데이터 수집 (KRX Open API → DART → pykrx 폴백)
│   ├── dart_client.py          ← DART OpenAPI 클라이언트
│   ├── processor.py            ← 이상치·결측치 처리
│   └── storage.py              ← SQLite CRUD (WAL 모드, 벌크 쿼리)
│
├── factors/
│   ├── __init__.py
│   ├── value.py                ← PBR·PER·DIV 스코어
│   ├── momentum.py             ← 12M 모멘텀 스코어
│   ├── quality.py              ← ROE·부채비율 스코어
│   └── composite.py            ← 멀티팩터 합산 + 필터
│
├── strategy/
│   ├── __init__.py
│   ├── screener.py             ← 종목 스크리닝 통합
│   ├── rebalancer.py           ← 리밸런싱 로직 + 시장충격 모델
│   └── market_regime.py        ← 시장 레짐 필터 (200일 이평선 기반)
│
├── backtest/
│   ├── __init__.py
│   ├── engine.py               ← 월별 리밸런싱 백테스트
│   ├── metrics.py              ← CAGR·MDD·Sharpe 계산
│   └── report.py               ← quantstats HTML 리포트
│
├── trading/
│   ├── __init__.py
│   ├── kiwoom_api.py           ← 키움 REST API 클라이언트
│   └── order.py                ← 주문 실행기
│
├── notify/
│   ├── __init__.py
│   └── telegram.py             ← 텔레그램 알림
│
├── dashboard/
│   └── app.py                  ← Streamlit 대시보드
│
├── gui/                        ← PyQt6 GUI 애플리케이션
│   ├── __main__.py             ← python -m gui 진입점
│   ├── app.py                  ← QApplication 초기화
│   ├── main_window.py          ← 메인 윈도우 (탭 레이아웃)
│   ├── themes.py               ← 다크/라이트 테마
│   ├── tray_icon.py            ← 시스템 트레이 아이콘
│   └── widgets/                ← UI 위젯 (스케줄러, 백테스트, 포트폴리오 등)
│
├── scheduler/
│   └── main.py                 ← APScheduler 자동 실행
│
├── tests/                      ← 단위/통합 테스트 (15개 파일, 335개 테스트)
│
├── notebooks/
│
├── build_exe.py                ← PyInstaller exe 빌드 스크립트
├── run_backtest.py             ← 백테스트 CLI 진입점
│
└── logs/
    └── quant.log
```

---

## 2-3. config/settings.py

```python
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
        assert abs(total - 1.0) < 1e-9, f"팩터 가중치 합이 1이 아닙니다: {total}"


@dataclass
class ValueWeights:
    """밸류 팩터 내 세부 지표 가중치"""
    pbr: float = 0.50
    per: float = 0.30
    div: float = 0.20


@dataclass
class UniverseConfig:
    market: str = "KOSPI"
    min_market_cap_percentile: float = 10.0   # 시가총액 하위 10% 제외
    exclude_finance: bool = True               # 금융주 제외
    min_listing_days: int = 365               # 상장 1년 미만 제외


@dataclass
class PortfolioConfig:
    n_stocks: int = 30
    weight_method: str = "equal"             # equal / value_weighted


@dataclass
class TradingConfig:
    commission_rate: float = 0.00015         # 수수료 0.015%
    tax_rate: float = 0.0018                 # 거래세 0.18% (매도만)
    slippage: float = 0.001                  # 슬리피지 0.1%


@dataclass
class Settings:
    factor_weights: FactorWeights = field(default_factory=FactorWeights)
    value_weights: ValueWeights = field(default_factory=ValueWeights)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)

    # 키움 REST API
    kiwoom_app_key: str = field(
        default_factory=lambda: os.getenv("KIWOOM_APP_KEY", ""))
    kiwoom_app_secret: str = field(
        default_factory=lambda: os.getenv("KIWOOM_APP_SECRET", ""))
    kiwoom_account_no: str = field(
        default_factory=lambda: os.getenv("KIWOOM_ACCOUNT_NO", ""))
    is_paper_trading: bool = field(
        default_factory=lambda: os.getenv("IS_PAPER_TRADING", "True").strip() == "True")

    # 텔레그램
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # 내부 경로
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "data/quant.db"))
    log_path: str = field(
        default_factory=lambda: os.getenv("LOG_PATH", "logs/quant.log"))


# 전역 싱글톤
settings = Settings()
```

---

## 2-4. 로깅 설정

```python
# config/logging_config.py
import logging
import logging.handlers
import os
from config.settings import settings


def setup_logging():
    """프로젝트 전역 로깅 설정"""
    os.makedirs(os.path.dirname(settings.log_path), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            # 콘솔 출력
            logging.StreamHandler(),
            # 파일 저장 (10MB × 5개 롤링)
            logging.handlers.RotatingFileHandler(
                settings.log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8"
            ),
        ]
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pykrx").setLevel(logging.WARNING)
```

---

## 2-5. 의존성 검증 스크립트

```bash
# 환경 구축 후 실행하여 정상 여부 확인
python -c "
import pandas as pd, numpy as np, pykrx, requests, sqlalchemy
import quantstats, streamlit, APScheduler
print('✅ 모든 핵심 패키지 임포트 성공')
print(f'  pandas: {pd.__version__}')
print(f'  numpy: {np.__version__}')
"
```
