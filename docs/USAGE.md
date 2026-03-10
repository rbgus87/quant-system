# 실행 가이드 — 한국 주식 멀티팩터 퀀트 자동매매 시스템

## 목차

1. [환경 설정](#1-환경-설정)
2. [실행 명령어 요약](#2-실행-명령어-요약)
3. [상세 사용법](#3-상세-사용법)
4. [프로젝트 구조](#4-프로젝트-구조)
5. [설정 변경](#5-설정-변경)
6. [문제 해결](#6-문제-해결)

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

# 내부 경로 (선택, 기본값 사용 가능)
DB_PATH=data/quant.db
LOG_PATH=logs/quant.log
```

| 변수 | 필수 | 기본값 | 설명 |
|------|:----:|--------|------|
| `KIWOOM_APP_KEY` | O | - | 키움 REST API 앱 키 |
| `KIWOOM_APP_SECRET` | O | - | 키움 REST API 시크릿 |
| `KIWOOM_ACCOUNT_NO` | O | - | 키움 계좌번호 |
| `IS_PAPER_TRADING` | - | `True` | `True`=모의투자, `False`=실전투자 |
| `TELEGRAM_BOT_TOKEN` | O | - | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | O | - | 알림 받을 채팅 ID |
| `DB_PATH` | - | `data/quant.db` | SQLite DB 파일 경로 |
| `LOG_PATH` | - | `logs/quant.log` | 로그 파일 경로 |

---

## 2. 실행 명령어 요약

> 모든 명령어는 **프로젝트 루트** (`D:\project\quant-system`)에서 **가상환경 활성화 후** 실행합니다.

### 핵심 명령어

| 구분 | 명령어 | 설명 |
|------|--------|------|
| **자동매매 스케줄러** | `python scheduler/main.py` | 상주 프로세스 (리밸런싱 + 리포트) |
| **스케줄러 설정 확인** | `python scheduler/main.py --dry-run` | 실행 없이 설정만 검증 |
| **백테스트 (전체)** | `python run_backtest.py` | In-Sample + Out-of-Sample |
| **백테스트 (인샘플)** | `python run_backtest.py --mode insample` | 2015~2020 구간 |
| **백테스트 (아웃샘플)** | `python run_backtest.py --mode outsample` | 2021~2024 구간 |
| **백테스트 (자금 지정)** | `python run_backtest.py --cash 50000000` | 초기자금 변경 |
| **대시보드** | `streamlit run dashboard/app.py` | 웹 모니터링 대시보드 |

### 개발/테스트 명령어

| 구분 | 명령어 | 설명 |
|------|--------|------|
| **전체 테스트** | `pytest` | 모든 테스트 실행 |
| **상세 테스트** | `pytest -v` | 개별 테스트 결과 표시 |
| **특정 모듈 테스트** | `pytest tests/test_backtest.py` | 단일 파일 테스트 |
| **특정 테스트 함수** | `pytest tests/test_backtest.py::test_함수명` | 단일 함수 테스트 |
| **코드 포맷팅** | `black .` | 전체 코드 자동 포맷 |
| **린트 검사** | `ruff check .` | 코드 품질 검사 |

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
| 매 영업일(월~금) 08:50 | 이번 달 마지막 영업일일 때만 | 멀티팩터 스크리닝 → 리밸런싱 주문 |
| 매 영업일(월~금) 15:35 | 항상 | 일별 수익 리포트 텔레그램 발송 |

**리밸런싱 프로세스:**
1. 멀티팩터 스크리닝으로 신규 포트폴리오 30종목 선정
2. 현재 보유 종목과 비교
3. 제외 종목 매도 → 신규 종목 매수 주문 실행
4. 결과를 텔레그램으로 알림

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
- 콘솔: CAGR, MDD, 샤프비율 등 성과 지표
- HTML 리포트: `reports/insample_report.html`, `reports/outsample_report.html`
- KOSPI 벤치마크 대비 성과 비교 포함

### 3-3. 모니터링 대시보드

```bash
streamlit run dashboard/app.py
```

- 브라우저에서 `http://localhost:8501` 자동 열림
- 키움 API 연동하여 실시간 계좌 정보 표시
- 총 평가금액, 예수금, 보유 종목 수, 총 손익 KPI
- 현재 포트폴리오 종목 테이블
- 60초 캐시로 API 호출 최소화

### 3-4. 테스트

```bash
# 전체 테스트
pytest

# 상세 출력
pytest -v

# 모듈별 테스트
pytest tests/test_collector.py     # 데이터 수집
pytest tests/test_factors.py       # 팩터 계산
pytest tests/test_backtest.py      # 백테스트 엔진
pytest tests/test_kiwoom_api.py    # 키움 API
pytest tests/test_order.py         # 주문 실행
pytest tests/test_screener.py      # 종목 스크리닝
pytest tests/test_storage.py       # DB 저장
pytest tests/test_telegram.py      # 텔레그램 알림
pytest tests/test_scheduler.py     # 스케줄러
pytest tests/test_processor.py     # 데이터 전처리
pytest tests/test_integration.py   # 통합 테스트
```

---

## 4. 프로젝트 구조

```
quant-system/
├── config/                 # 전역 설정
│   ├── settings.py         #   팩터 가중치, 유니버스, 매매 설정
│   └── logging_config.py   #   로깅 설정 (10MB × 5 롤링)
│
├── data/                   # 데이터 파이프라인
│   ├── collector.py        #   KRX 데이터 수집 (pykrx)
│   ├── processor.py        #   데이터 전처리
│   └── storage.py          #   SQLite DB 저장/조회
│
├── factors/                # 팩터 계산
│   ├── value.py            #   밸류 팩터 (PBR, PER, 배당률)
│   ├── momentum.py         #   모멘텀 팩터
│   ├── quality.py          #   퀄리티 팩터
│   └── composite.py        #   멀티팩터 합성 스코어
│
├── strategy/               # 전략
│   ├── screener.py         #   멀티팩터 종목 스크리닝
│   └── rebalancer.py       #   리밸런싱 로직
│
├── backtest/               # 백테스트
│   ├── engine.py           #   백테스트 엔진
│   ├── metrics.py          #   성과 분석 (CAGR, MDD, 샤프)
│   └── report.py           #   HTML 리포트 생성 (quantstats)
│
├── trading/                # 실전 매매
│   ├── kiwoom_api.py       #   키움 REST API 클라이언트
│   └── order.py            #   주문 실행기
│
├── notify/                 # 알림
│   └── telegram.py         #   텔레그램 봇 알림
│
├── scheduler/              # 자동화
│   └── main.py             #   APScheduler 스케줄러 (진입점)
│
├── dashboard/              # 모니터링
│   └── app.py              #   Streamlit 대시보드
│
├── tests/                  # 테스트 (11개 파일)
├── docs/                   # 문서
├── logs/                   # 로그 파일
├── reports/                # 백테스트 HTML 리포트 출력
│
├── run_backtest.py         # 백테스트 CLI 진입점
├── requirements.txt        # pip 의존성
├── .env                    # 환경변수 (gitignore)
└── .env.example            # 환경변수 템플릿
```

---

## 5. 설정 변경

### 5-1. 팩터 가중치 (`config/settings.py`)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 밸류 가중치 | 0.40 (40%) | PBR 50% + PER 30% + 배당률 20% |
| 모멘텀 가중치 | 0.40 (40%) | - |
| 퀄리티 가중치 | 0.20 (20%) | - |

> 세 가중치의 합은 반드시 1.0이어야 합니다.

### 5-2. 유니버스 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 시장 | KOSPI | 대상 시장 |
| 시가총액 하위 제외 | 10% | 소형주 제외 비율 |
| 금융주 제외 | true | 금융업종 제외 여부 |
| 최소 상장일 | 365일 | 신규 상장 제외 기간 |

### 5-3. 포트폴리오 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 종목 수 | 30개 | 포트폴리오 편입 종목 수 |
| 비중 방식 | equal | 동일 비중 (equal / value_weighted) |

### 5-4. 거래 비용

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 수수료율 | 0.015% | 매수/매도 수수료 |
| 거래세 | 0.18% | 매도 시만 적용 |
| 슬리피지 | 0.1% | 체결가 차이 |

---

## 6. 문제 해결

### 자주 발생하는 오류

| 증상 | 원인 | 해결 |
|------|------|------|
| `ModuleNotFoundError` | 가상환경 미활성화 | `venv\Scripts\activate` 실행 |
| `KIWOOM_APP_KEY 없음` | `.env` 미설정 | `.env.example` 복사 후 값 입력 |
| API 연결 실패 | IP 미등록 또는 키 만료 | 키움 API 콘솔에서 IP 등록 확인 |
| 텔레그램 전송 실패 | 봇 토큰/채팅 ID 오류 | BotFather에서 토큰 재확인 |
| `AssertionError: 팩터 가중치` | 가중치 합 != 1.0 | `settings.py`에서 합계 1.0 맞추기 |
| `streamlit` 실행 안됨 | 패키지 미설치 | `pip install -r requirements.txt` |

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

### 모의투자 → 실전투자 전환

1. `.env`에서 `IS_PAPER_TRADING=False`로 변경
2. 키움 API 콘솔에서 실전투자용 앱 키 발급
3. 스케줄러 재시작
4. **주의**: 실전 모드에서는 실제 주문이 체결됩니다
