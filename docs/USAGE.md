# 실행 가이드 — 한국 주식 멀티팩터 퀀트 자동매매 시스템

## 목차

1. [환경 설정](#1-환경-설정)
2. [실행 명령어 요약](#2-실행-명령어-요약)
3. [상세 사용법](#3-상세-사용법)
4. [프로젝트 구조](#4-프로젝트-구조)
5. [설정 변경](#5-설정-변경)
6. [안전장치](#6-안전장치)
7. [권장 실행 순서](#7-권장-실행-순서)
8. [문제 해결](#8-문제-해결)

---

## 1. 환경 설정

### 1-1. 사전 요구사항

| 항목 | 버전 | 비고 |
|------|------|------|
| Python | 3.14+ | `python --version`으로 확인 |
| OS | Windows 10/11 x64 | 키움 API IP 등록 필요 |
| pip | 최신 | `pip install --upgrade pip` |

### 1-2. 초기 설치

```bash
# 1) 가상환경 생성 (최초 1회)
python -m venv venv

# 2) 가상환경 활성화 (매 세션마다)
venv\Scripts\activate          # Windows CMD
# 또는
source venv/Scripts/activate   # Git Bash / WSL

# 3) 의존성 설치
pip install -r requirements.txt
```

> 활성화 성공 시 프롬프트 앞에 `(venv)`가 표시됩니다.
> **모든 실행 명령어는 가상환경 활성화 상태에서 실행해야 합니다.**

### 1-3. 환경변수 설정

```bash
# .env.example을 복사하여 .env 생성
copy .env.example .env         # Windows CMD
# 또는
cp .env.example .env           # Git Bash
```

`.env` 파일을 열어 실제 값으로 수정:

```env
# 키움 REST API (https://api.kiwoom.com 에서 발급)
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿키
KIWOOM_ACCOUNT_NO=계좌번호

# 실전/모의 구분 (반드시 True로 시작, 검증 완료 전 변경 금지)
IS_PAPER_TRADING=True

# 텔레그램 (https://t.me/BotFather 에서 봇 생성)
TELEGRAM_BOT_TOKEN=봇_토큰
TELEGRAM_CHAT_ID=채팅_ID

# KRX Open API (선택)
KRX_OPENAPI_KEY=KRX_API키

# 내부 경로 (선택, 기본값 사용 가능)
DB_PATH=data/quant.db
LOG_PATH=logs/quant.log
LOG_LEVEL=INFO
```

| 변수 | 필수 | 기본값 | 설명 |
|------|:----:|--------|------|
| `KIWOOM_APP_KEY` | O | - | 키움 REST API 앱 키 |
| `KIWOOM_APP_SECRET` | O | - | 키움 REST API 시크릿 |
| `KIWOOM_ACCOUNT_NO` | O | - | 키움 계좌번호 |
| `IS_PAPER_TRADING` | - | `True` | `True`=모의투자, `False`=실전투자 |
| `TELEGRAM_BOT_TOKEN` | O | - | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | - | - | 알림 받을 채팅 ID |
| `KRX_OPENAPI_KEY` | - | - | KRX Open API 인증키 |
| `DB_PATH` | - | `data/quant.db` | SQLite DB 파일 경로 |
| `LOG_PATH` | - | `logs/quant.log` | 로그 파일 경로 |
| `LOG_LEVEL` | - | `INFO` | 로그 레벨 (`DEBUG` / `INFO` / `WARNING`) |

---

## 2. 실행 명령어 요약

> 모든 명령어는 **프로젝트 루트** (`D:\project\quant-system`)에서 **가상환경 활성화 후** 실행합니다.

### 핵심 명령어

| 구분 | 명령어 | 설명 |
|------|--------|------|
| **자동매매 스케줄러** | `python scheduler/main.py` | 상주 프로세스 (리밸런싱 + 리포트) |
| **즉시 리밸런싱** | `python scheduler/main.py --now` | 월말 체크 무시, 즉시 1회 리밸런싱 |
| **스크리닝만 확인** | `python scheduler/main.py --screen-only` | 매매 없이 종목 목록만 출력 |
| **스케줄러 설정 확인** | `python scheduler/main.py --dry-run` | 실행 없이 설정만 검증 |
| **백테스트 (전체)** | `python run_backtest.py` | In-Sample + Out-of-Sample |
| **백테스트 (인샘플)** | `python run_backtest.py --mode insample` | 2015~2020 구간 |
| **백테스트 (아웃샘플)** | `python run_backtest.py --mode outsample` | 2021~2024 구간 |
| **백테스트 (자금 지정)** | `python run_backtest.py --cash 50000000` | 초기자금 변경 |
| **대시보드** | `streamlit run dashboard/app.py` | 웹 모니터링 대시보드 |
| **GUI 앱** | `python -m gui` | PyQt6 데스크탑 앱 (스케줄러 제어, 백테스트, 포트폴리오) |
| **GUI (exe)** | `KoreanQuant.exe` | PyInstaller 빌드된 실행 파일 |

### 개발/테스트 명령어

| 구분 | 명령어 | 설명 |
|------|--------|------|
| **전체 테스트** | `python -m pytest tests/` | 335개 테스트 실행 |
| **상세 테스트** | `python -m pytest tests/ -v` | 개별 테스트 결과 표시 |
| **특정 모듈 테스트** | `python -m pytest tests/test_backtest.py` | 단일 파일 테스트 |
| **실패 시 즉시 중단** | `python -m pytest tests/ -x --tb=short` | 첫 실패에서 멈춤 |
| **코드 포맷팅** | `black .` | 전체 코드 자동 포맷 |
| **린트 검사** | `ruff check .` | 코드 품질 검사 |
| **린트 자동 수정** | `ruff check . --fix` | 자동 수정 가능한 것 수정 |

---

## 3. 상세 사용법

### 3-1. 자동매매 스케줄러

프로그램이 상주하면서 정해진 시간에 자동으로 작업을 수행합니다.

```bash
# 기본 실행 (Ctrl+C로 종료)
python scheduler/main.py

# 실행 전 설정 점검만 수행
python scheduler/main.py --dry-run
```

**자동 스케줄:**

| 시간 | 조건 | 동작 |
|------|------|------|
| 매 영업일(월~금) 08:50 | 이번 달 마지막 KRX 거래일일 때만 | 멀티팩터 스크리닝 → 리밸런싱 주문 |
| 매 영업일(월~금) 15:15 | 항상 | 일별 방어 체크 (MDD 서킷브레이커 + 트레일링 스톱) |
| 매 영업일(월~금) 15:35 | 항상 | 일별 수익 리포트 텔레그램 발송 |

**리밸런싱 프로세스:**
1. 멀티팩터 스크리닝으로 신규 포트폴리오 선정 (종목 수는 config.yaml 프리셋에 따라 결정)
2. 현재 보유 종목과 비교하여 매도/매수 목록 생성
3. 안전장치 검증 (턴오버 제한, MDD 서킷브레이커)
4. 매도 먼저 실행 → 체결 확인 → 매수 실행 (동일 비중)
5. 결과를 텔레그램으로 알림

**수동 실행 옵션:**

월말을 기다리지 않고 즉시 실행할 수 있습니다.

```bash
# 종목 스크리닝만 확인 (매매 없음, 안전)
python scheduler/main.py --screen-only

# 즉시 리밸런싱 (월말 체크 무시, 실제 주문 발생)
python scheduler/main.py --now
```

- `--screen-only`: 현재 시점 기준 멀티팩터 스크리닝을 실행하고 선정 종목과 복합스코어를 출력합니다. 매매는 발생하지 않으므로 언제든 안전하게 실행 가능합니다.
- `--now`: 월말 여부와 관계없이 즉시 1회 리밸런싱을 실행합니다. 실제 매도/매수 주문이 발생하므로 주의가 필요합니다. 실행 후 자동 종료됩니다.
- `--dry-run`: 스케줄러를 시작하지 않고 모의/실전 모드, 텔레그램, 키움 API 설정 상태만 확인합니다.

**데이터 소스:**

실전에서 프로그램 실행 시 **매번 최신 데이터를 실시간 조회**합니다:
- 펀더멘털/시가총액: KRX Open API에서 당일 데이터 실시간 조회
- 모멘텀용 OHLCV: API로 가져오되 SQLite DB를 캐시로 활용 (과거 가격은 재요청 안 함)
- 잔고/주문: 키움 REST API로 실시간 처리

SQLite DB(`data/quant.db`)는 캐시 레이어이며, 백테스트에서 수집된 오래된 데이터로 판단하지 않습니다.

### 3-2. 백테스트

과거 데이터로 전략 성과를 검증합니다.

```bash
# 전체 구간 (In-Sample + Out-of-Sample)
python run_backtest.py

# In-Sample만 (2015-01-01 ~ 2020-12-31)
python run_backtest.py --mode insample

# Out-of-Sample만 (2021-01-01 ~ 2024-12-31)
python run_backtest.py --mode outsample

# 초기자금 5천만원으로 변경 (기본: 1천만원)
python run_backtest.py --cash 50000000

# 조합 사용
python run_backtest.py --mode insample --cash 50000000
```

**옵션:**

| 옵션 | 값 | 기본값 | 설명 |
|------|-----|--------|------|
| `--mode` | `insample` / `outsample` / `both` | `both` | 백테스트 구간 |
| `--cash` | 숫자 | `10000000` | 초기 투자금 (원) |

**출력물:**
- 콘솔: CAGR, MDD, 샤프비율, 소르티노, VaR(95%), 칼마비율, 승률, MDD 회복기간
- HTML 리포트: `reports/insample_report.html`, `reports/outsample_report.html`
- KOSPI 벤치마크 대비 초과수익률, 정보비율 포함
- 턴오버 로그 (리밸런싱별 교체율)

**성과 지표 설명:**

| 지표 | 설명 |
|------|------|
| CAGR | 연 복합 수익률 |
| Total Return | 기간 총 수익률 |
| Volatility | 연환산 변동성 |
| MDD | 최대 낙폭 (고점 대비 최대 하락) |
| Sharpe | 샤프 비율 (위험 대비 초과수익) |
| Sortino | 소르티노 비율 (하방 위험 대비 초과수익) |
| Calmar | 칼마 비율 (CAGR / MDD) |
| VaR(95%) | 95% 신뢰수준 일일 최대 손실 |
| Win Rate | 양수 수익 일 비율 |
| MDD Recovery | MDD 이후 고점 회복까지 거래일 수 |
| Excess Return | 벤치마크 대비 초과 CAGR |
| Information Ratio | 초과수익 / 추적오차 |

### 3-3. 워크-포워드 검증

백테스트 과적합 여부를 확인하려면 Python에서 직접 호출합니다:

```python
from backtest.engine import MultiFactorBacktest

engine = MultiFactorBacktest(initial_cash=10_000_000)
results = engine.walk_forward(
    start_date="2015-01-01",
    end_date="2024-12-31",
    n_splits=3,          # 3구간 분할
    train_ratio=0.7,     # 70% 학습 / 30% 검증
)

for r in results:
    print(f"구간 {r['split']}: Train CAGR={r['train_cagr']:.2%}, Test CAGR={r['test_cagr']:.2%}")
```

Train CAGR과 Test CAGR의 갭이 클수록 과적합 가능성이 높습니다.

### 3-4. 월별/연도별 수익률 분석

```python
from backtest.metrics import PerformanceAnalyzer
import pandas as pd

analyzer = PerformanceAnalyzer()

# 백테스트 결과 로드 후
monthly_table = analyzer.monthly_returns(result["portfolio_value"])
yearly = analyzer.yearly_returns(result["portfolio_value"])

print(monthly_table)  # 행=연도, 열=월, 연간 합산 포함
print(yearly)         # 연도별 수익률
```

### 3-5. 모니터링 대시보드

```bash
streamlit run dashboard/app.py
```

- 브라우저에서 `http://localhost:8501` 자동 열림
- 키움 API 연동하여 실시간 계좌 정보 표시
- 총 평가금액, 예수금, 보유 종목 수, 총 손익 KPI
- 현재 포트폴리오 종목 테이블
- 60초 캐시로 API 호출 최소화

### 3-6. GUI 애플리케이션

PyQt6 기반 데스크탑 앱으로 스케줄러 제어, 백테스트, 포트폴리오 조회 등을 GUI에서 수행할 수 있습니다.

```bash
# Python에서 직접 실행
python -m gui

# PyInstaller로 빌드된 exe 실행
KoreanQuant.exe
```

**주요 기능:**
- 다크/라이트 테마 전환
- 스케줄러 시작/종료 제어
- 백테스트 파라미터 입력 및 실행
- 프리셋 선택 및 YAML 저장
- 현재 포트폴리오 조회 (키움 API 연동)
- 실시간 로그 뷰어
- 긴급 전량 매도 (수동 리밸런싱 우회)
- 시스템 트레이 최소화

**exe 빌드:**

```bash
python build_exe.py
# dist/KoreanQuant.exe 생성
```

### 3-7. 테스트

```bash
# 전체 테스트 (335개)
python -m pytest tests/

# 상세 출력
python -m pytest tests/ -v

# 모듈별 테스트
python -m pytest tests/test_collector.py     # 데이터 수집
python -m pytest tests/test_factors.py       # 팩터 계산
python -m pytest tests/test_backtest.py      # 백테스트 엔진
python -m pytest tests/test_kiwoom_api.py    # 키움 API
python -m pytest tests/test_order.py         # 주문 실행 + 안전장치
python -m pytest tests/test_screener.py      # 종목 스크리닝
python -m pytest tests/test_storage.py       # DB 저장
python -m pytest tests/test_telegram.py      # 텔레그램 알림
python -m pytest tests/test_scheduler.py     # 스케줄러
python -m pytest tests/test_processor.py     # 데이터 전처리
python -m pytest tests/test_integration.py   # 통합 테스트
python -m pytest tests/test_smoke.py         # 스모크 테스트
python -m pytest tests/test_settings.py      # 설정 로드/검증
python -m pytest tests/test_dart_client.py   # DART API 클라이언트
python -m pytest tests/test_market_regime.py # 시장 레짐 필터
```

---

## 4. 프로젝트 구조

```
quant-system/
├── config/                 # 전역 설정
│   ├── settings.py         #   팩터 가중치, 유니버스, 매매 설정
│   ├── calendar.py         #   KRX 거래일 캘린더 (한국 공휴일 인식)
│   └── logging_config.py   #   로깅 설정 (10MB x 5 롤링)
│
├── data/                   # 데이터 파이프라인
│   ├── collector.py        #   KRX 데이터 수집 (pykrx)
│   ├── processor.py        #   데이터 전처리 (이상치, 유동성, 거래정지 필터)
│   └── storage.py          #   SQLite DB 저장/조회
│
├── factors/                # 팩터 계산
│   ├── value.py            #   밸류 팩터 (PBR 50% + PER 30% + 배당률 20%)
│   ├── momentum.py         #   모멘텀 팩터 (12개월, 최근 1개월 제외)
│   ├── quality.py          #   퀄리티 팩터 (ROE 40% + EY 30% + 배당 30%)
│   └── composite.py        #   멀티팩터 합성 (union 기반, 2/3 이상 팩터 필요)
│
├── strategy/               # 전략
│   ├── screener.py         #   멀티팩터 종목 스크리닝 (KOSPI/KOSDAQ/ALL)
│   └── rebalancer.py       #   리밸런싱 로직 + 시장충격 모델
│
├── backtest/               # 백테스트
│   ├── engine.py           #   백테스트 엔진 (월별 리밸런싱, 워크-포워드)
│   ├── metrics.py          #   성과 분석 (12개 지표 + 팩터 귀인)
│   └── report.py           #   HTML 리포트 생성 (quantstats)
│
├── trading/                # 실전 매매
│   ├── kiwoom_api.py       #   키움 REST API 클라이언트 (rate limiting)
│   └── order.py            #   주문 실행기 (안전장치 내장)
│
├── notify/                 # 알림
│   └── telegram.py         #   텔레그램 봇 알림 (재시도 + 청크 분할)
│
├── scheduler/              # 자동화
│   └── main.py             #   APScheduler 스케줄러 (진입점)
│
├── dashboard/              # 모니터링
│   └── app.py              #   Streamlit 대시보드
│
├── gui/                    # PyQt6 GUI 애플리케이션
│   ├── __main__.py         #   python -m gui 진입점
│   ├── app.py              #   QApplication 초기화
│   ├── main_window.py      #   메인 윈도우 (탭 레이아웃)
│   ├── themes.py           #   다크/라이트 테마
│   ├── tray_icon.py        #   시스템 트레이 아이콘
│   └── widgets/            #   UI 위젯 (백테스트, 포트폴리오, 스케줄러, 로그 등)
│
├── tests/                  # 테스트 (15개 파일, 335개 테스트)
├── docs/                   # 문서
├── logs/                   # 로그 파일
├── reports/                # 백테스트 HTML 리포트 출력
│
├── run_backtest.py         # 백테스트 CLI 진입점
├── build_exe.py            # PyInstaller exe 빌드 스크립트
├── requirements.txt        # pip 의존성
├── .env                    # 환경변수 (gitignore)
└── .env.example            # 환경변수 템플릿
```

---

## 5. 설정 변경

모든 설정은 `config/settings.py`에서 중앙 관리됩니다.

### 5-1. 팩터 가중치

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 밸류 가중치 | 0.40 (40%) | PBR 50% + PER 30% + 배당률 20% |
| 모멘텀 가중치 | 0.40 (40%) | 멀티기간: 12M 60% + 6M 30% + 3M 10% (최근 1개월 제외) |
| 퀄리티 가중치 | 0.20 (20%) | ROE 40% + EY 30% + 배당 30% |

> 세 가중치의 합은 반드시 1.0이어야 합니다.

### 5-2. 유니버스 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 시장 | `KOSPI` | `KOSPI` / `KOSDAQ` / `ALL` (통합) |
| 시가총액 하위 제외 | 10% | 소형주 제외 비율 |
| 금융주 제외 | true | 금융업종 제외 여부 |
| 최소 상장일 | 365일 | 신규 상장 제외 기간 |
| 최소 평균 거래대금 | 1억원 | 20일 평균 거래대금 하한 |

### 5-3. 포트폴리오 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 종목 수 | 30개 | 포트폴리오 편입 종목 수 (config.yaml 프리셋으로 조정 가능) |
| 비중 방식 | equal | 동일 비중 (equal / value_weighted) |

### 5-4. 거래 비용 및 안전장치

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 수수료율 | 0.015% | 매수/매도 수수료 |
| 거래세 | 0.18% | 매도 시만 적용 |
| 슬리피지 | 0.1% | 체결가 차이 |
| 단일 종목 최대 비중 | 10% | 집중 투자 방지 |
| 월간 최대 교체율 | 50% | 과도한 리밸런싱 방지 |
| MDD 서킷 브레이커 | -30% | 고점 대비 30% 이상 하락 시 리밸런싱 중단 |

---

## 6. 안전장치

시스템에 내장된 안전장치 목록입니다. 모두 자동으로 작동합니다.

### 주문 실행 안전장치 (`trading/order.py`)

| 안전장치 | 동작 | 예외 |
|----------|------|------|
| **잔고 검증** | 매도 전/매수 전 잔고 API 결과 검증 | `BalanceValidationError` 발생 시 전체 중단 |
| **턴오버 제한** | 교체율 > 50%이면 중단 | `TurnoverLimitExceeded` (스크리너 데이터 이상 방지) |
| **MDD 서킷 브레이커** | 고점 대비 -30% 이상 하락 시 리밸런싱 중단 | `DrawdownCircuitBreaker` |
| **단일 종목 비중 제한** | 한 종목이 포트폴리오의 10% 초과 불가 | 자동 클리핑 |
| **매도 체결 확인** | 주문번호 기반 미체결 확인 후 매수 진행 | 30초 타임아웃 |
| **모의투자 경고** | 실전 모드 시 WARNING 로그 출력 | - |
| **전량 청산 경고** | 목표 포트폴리오 비어있을 때 WARNING 출력 | - |

### 데이터 안전장치

| 안전장치 | 동작 |
|----------|------|
| **거래정지 종목 필터** | 당일 거래량 0인 종목 자동 제외 |
| **유동성 필터** | 20일 평균 거래대금 1억 미만 종목 제외 |
| **이상치 처리** | PBR/PER 양방향 1% Winsorize |
| **KRX 캘린더** | 한국 공휴일 자동 인식 (exchange_calendars) |
| **T+1 체결** | 선견 편향 방지 (신호일 다음 영업일 시가 체결) |

### API 안전장치

| 안전장치 | 동작 |
|----------|------|
| **Rate Limiting** | API 요청 간 최소 0.2초 간격 유지 |
| **자동 재시도** | GET/POST 실패 시 최대 3회 지수 백오프 재시도 |
| **토큰 자동 갱신** | 만료 10분 전 자동 재발급 |
| **텔레그램 재시도** | 429 에러 시 Retry-After 대기, 타임아웃 백오프 |

---

## 7. 권장 실행 순서

처음 사용하는 경우 아래 순서를 따르세요:

### Step 1: 환경 검증

```bash
# 가상환경 활성화
venv\Scripts\activate

# 테스트 실행 (정상 동작 확인)
python -m pytest tests/ -x --tb=short
```

335개 테스트가 모두 통과해야 합니다.

### Step 2: 백테스트 (전략 성과 확인)

```bash
python run_backtest.py
```

`reports/` 폴더에 생성된 HTML 리포트를 브라우저로 열어 성과를 확인합니다.

### Step 3: 설정 검증 (Dry Run)

```bash
python scheduler/main.py --dry-run
```

키움 API, 텔레그램 설정이 올바르게 되어 있는지 확인합니다.

### Step 4: 모의투자 시작

```bash
# .env에서 IS_PAPER_TRADING=True 확인 후
python scheduler/main.py
```

최소 1~2개월 모의투자를 진행하며 리밸런싱 로그와 텔레그램 알림을 모니터링합니다.

### Step 5: 실전투자 전환

```
실전 전환 전 체크리스트:
[ ] 모의투자 1개월 이상 정상 운영 확인
[ ] 백테스트 성과가 기대 수준인지 확인
[ ] 키움 API 실전용 앱 키 별도 발급
[ ] .env에서 IS_PAPER_TRADING=False로 변경
[ ] 스케줄러 재시작
```

> **주의**: `IS_PAPER_TRADING=False` 설정 시 실제 주문이 체결됩니다.
> 실전 모드에서는 `api.kiwoom.com`으로 연결되며, 모의 모드에서는 `mockapi.kiwoom.com`으로 연결됩니다.

---

## 8. 문제 해결

### 자주 발생하는 오류

| 증상 | 원인 | 해결 |
|------|------|------|
| `ModuleNotFoundError` | 가상환경 미활성화 | `venv\Scripts\activate` 실행 |
| `KIWOOM_APP_KEY 없음` | `.env` 미설정 | `.env.example` 복사 후 값 입력 |
| API 연결 실패 | IP 미등록 또는 키 만료 | 키움 API 콘솔에서 IP 등록 확인 |
| 텔레그램 전송 실패 | 봇 토큰/채팅 ID 오류 | BotFather에서 토큰 재확인 |
| `ValueError: 팩터 가중치 합이 1이 아닙니다` | 가중치 합 != 1.0 | `settings.py`에서 합계 1.0 맞추기 |
| `streamlit` 실행 안됨 | 패키지 미설치 | `pip install -r requirements.txt` |
| `TurnoverLimitExceeded` | 교체율 50% 초과 | 정상 안전장치. 데이터 이상 확인 |
| `DrawdownCircuitBreaker` | 포트폴리오 -30% 이상 하락 | 정상 안전장치. 시장 상황 확인 후 수동 판단 |
| `BalanceValidationError` | 잔고 API 응답 이상 | 키움 서버 상태 확인 후 재시도 |
| pykrx 데이터 조회 실패 | KRX 서버 점검 또는 과호출 | 잠시 후 재시도 (request_delay 조절) |

### 로그 확인

```bash
# 최근 로그 확인
type logs\quant.log              # Windows CMD
# 또는
cat logs/quant.log               # Git Bash

# 로그 실시간 모니터링
tail -f logs/quant.log           # Git Bash
```

- 로그 파일: `logs/quant.log`
- 최대 10MB, 5개 파일 롤링
- 포맷: `2026-03-10 08:50:00 [INFO] scheduler.main: 월말 리밸런싱 시작`
- `LOG_LEVEL=DEBUG`로 변경하면 상세 디버그 로그 출력

### 대시보드 접속 불가

```bash
# 포트 충돌 시 다른 포트로 실행
streamlit run dashboard/app.py --server.port 8502
```

### 키움 API 토큰 오류

- 토큰 만료: 자동 갱신되지만, 키 자체가 만료된 경우 키움 콘솔에서 재발급
- 모의투자는 `mockapi.kiwoom.com` 사용 (KRX 거래만 지원, NXT/SOR 미지원)
- 실전투자는 `api.kiwoom.com` 사용 (SOR 거래소 기본)
