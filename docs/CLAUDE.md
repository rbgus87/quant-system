# CLAUDE.md — Korean Multi-Factor Quant System

## 프로젝트 개요
한국 주식시장(KOSPI) 대상 멀티팩터 퀀트 자동매매 시스템.
밸류(PBR·PER·배당) × 모멘텀(12M) × 퀄리티(ROE) 복합 팩터로 종목 선정 후
**매월 마지막 영업일** 신호 계산 → **다음 영업일 시가** 자동 리밸런싱.

## 기술 스택
- **Python 3.11**, 가상환경(venv)
- **데이터**: pykrx (KRX 공식), FinanceDataReader (보조/벤치마크)
- **백테스트**: 자체 구현 (pandas 기반, 월별 리밸런싱)
- **성과 분석**: quantstats (HTML 리포트)
- **자동매매**: 키움 REST API
  - 운영: `https://api.kiwoom.com`
  - 모의: `https://mockapi.kiwoom.com` (KRX만 지원)
- **DB**: SQLite + SQLAlchemy
- **스케줄링**: APScheduler
- **알림**: python-telegram-bot v21 (async)
- **대시보드**: Streamlit

## 디렉토리 구조
```
korean-quant/
├── CLAUDE.md             ← 이 파일
├── PRD.md                ← 전략 명세 (전체 요구사항)
├── docs/                 ← 상세 개발 가이드
├── .env                  ← API 키 (git 제외)
├── requirements.txt
├── config/settings.py    ← 전역 설정 (팩터 가중치 등)
├── data/
│   ├── collector.py      ← pykrx 데이터 수집
│   ├── processor.py      ← 전처리 (이상치, 결측치)
│   └── storage.py        ← SQLite 저장/로드
├── factors/
│   ├── value.py          ← 밸류 팩터
│   ├── momentum.py       ← 모멘텀 팩터
│   ├── quality.py        ← 퀄리티 팩터
│   └── composite.py      ← 멀티팩터 합산
├── strategy/
│   ├── screener.py       ← 유니버스 필터 + 스크리닝
│   └── rebalancer.py     ← 리밸런싱 로직
├── backtest/
│   ├── engine.py         ← 백테스트 실행 엔진
│   ├── metrics.py        ← 성과 지표 (CAGR, MDD, Sharpe)
│   └── report.py         ← HTML 리포트 생성
├── trading/
│   ├── kiwoom_api.py     ← 키움 REST API 클라이언트
│   └── order.py          ← 주문 실행기 (리밸런싱)
├── notify/telegram.py    ← 텔레그램 알림
├── dashboard/app.py      ← Streamlit UI
├── scheduler/main.py     ← APScheduler 자동 실행
├── tests/                ← 단위 테스트
└── notebooks/            ← Jupyter 탐색용
```

## 핵심 전략 파라미터
```python
# 유니버스
MARKET = "KOSPI"
MIN_MARKET_CAP_PERCENTILE = 10  # 시가총액 하위 10% 제외
EXCLUDE_SECTORS = ["은행", "증권", "보험", "기타금융"]

# 팩터 가중치
FACTOR_WEIGHTS = {"value": 0.40, "momentum": 0.40, "quality": 0.20}

# 밸류 세부 가중치
VALUE_WEIGHTS = {"PBR": 0.50, "PER": 0.30, "DIV": 0.20}

# 포트폴리오
N_STOCKS = 30
WEIGHT_METHOD = "equal"
REBALANCE = "월 마지막 영업일 신호 → 다음 영업일 시가 체결"

# 거래 비용
COMMISSION = 0.00015   # 0.015%
TAX = 0.0018           # 0.18% (매도만)
SLIPPAGE = 0.001       # 0.10%
```

## 코딩 컨벤션 (필수)
- **타입 힌트** 모든 함수에 필수 (`def func(x: int) -> pd.DataFrame:`)
- **docstring** Args/Returns 형식으로 작성
- **에러 처리** `try/except` + `logger.error()` 필수, 절대 무시 금지
- **로깅** `logging.getLogger(__name__)` 사용, `print()` 금지
- **설정값** `config/settings.py`에서만 중앙 관리
- **환경 변수** `.env` 파일만, 코드 하드코딩 절대 금지

## 중요 주의사항
1. `IS_PAPER_TRADING=True` 확인 후 실전 주문 전환
2. 백테스트에서 `date_str` 기준으로 pykrx 종목 조회 → 생존 편향 방지 자동 처리됨
3. 모멘텀 계산: 12개월 전 ~ 2개월 전 수익률 (최근 1개월 제외)
4. pykrx 응답의 컬럼명은 **한글** (시가, 고가, 저가, 종가, 거래량) → 영문 rename 필수
5. 키움 REST API 토큰 응답 필드: `"token"` (access_token 아님)

## 현재 개발 진행 상태
- [x] Phase 1: 환경 구축
- [ ] Phase 2: 데이터 파이프라인
- [ ] Phase 3: 팩터 구현
- [ ] Phase 4: 백테스트
- [ ] Phase 5: 키움 REST API 연동
- [ ] Phase 6: 자동화
- [ ] Phase 7: 실전 투입

## 알려진 이슈 / 메모
- pykrx API 과호출 방지: 종목별 조회 시 0.3~0.5초 delay 필수
- quantstats: `qs.extend_pandas()` 호출 없이도 `qs.reports.html()` 단독 사용 가능
- APScheduler: `BackgroundScheduler` 사용, timezone='Asia/Seoul' 명시 필수
- pandas 2.2+: `freq="BME"` deprecated → `pd.offsets.BMonthEnd()` 사용 권장
