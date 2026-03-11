# 01. 개발 환경 설정

## 1-1. 필수 설치

### Python 3.14
```bash
# Windows: python.org에서 3.14.x 다운로드
# 설치 시 "Add Python to PATH" 반드시 체크

python --version   # Python 3.14.x 확인
python -m pip install --upgrade pip
```

### VSCode 확장
```
필수:
- Python (Microsoft)
- Pylance
- Jupyter
- GitLens
- autoDocstring

권장:
- Error Lens
- Rainbow CSV
```

### Claude Code CLI
```bash
# Node.js 18+ 먼저 설치 (nodejs.org)
npm install -g @anthropic-ai/claude-code
claude --version
claude          # 최초 실행 시 브라우저 인증
```

### Git
```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

---

## 1-2. 프로젝트 초기화

```bash
mkdir korean-quant && cd korean-quant

# 가상환경
python -m venv venv

# 활성화
venv\Scripts\activate       # Windows
source venv/bin/activate    # Mac/Linux

git init
```

---

## 1-3. requirements.txt

```text
# 실제 버전은 requirements.txt 참조 — 아래는 주요 의존성 목록
# 핵심
pandas, numpy, scipy

# 한국 주식 데이터
pykrx                    # KRX 개별 종목 OHLCV (Naver 기반)
pykrx-openapi            # KRX Open API (전종목 벌크 조회)
finance-datareader       # 보조/벤치마크 데이터

# DART 재무제표 (KRX 배치 API 대체)
# dart_client.py에서 requests로 직접 호출

# 시각화
matplotlib, seaborn, plotly

# 성과 분석
quantstats

# HTTP 요청 (키움 REST API)
requests

# 스케줄링
APScheduler

# 알림
python-telegram-bot      # v21 async

# 데이터베이스
sqlalchemy               # SQLite ORM

# 환경 변수 / 설정
python-dotenv, pyyaml

# KRX 영업일 캘린더
exchange-calendars

# 대시보드
streamlit

# 개발/테스트
pytest, black, ruff
```

> ⚠️ **vectorbt, backtrader, pyfolio-reloaded 제거**: 의존성 충돌 및 설치 복잡도 높음.
> 백테스트는 pandas 기반 자체 엔진으로 구현 (더 단순하고 커스텀 용이).

```bash
pip install -r requirements.txt
```

---

## 1-4. .env 파일

```bash
# .env (git에 절대 포함하지 말 것)

# 키움 REST API
KIWOOM_APP_KEY=your_appkey_here
KIWOOM_APP_SECRET=your_secretkey_here
KIWOOM_ACCOUNT_NO=1234567890     # 10자리 계좌번호

# 실전/모의 구분 (반드시 True로 시작)
IS_PAPER_TRADING=True

# 텔레그램
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 데이터 API (KRX 배치 API 차단 대체)
KRX_OPENAPI_KEY=your_krx_api_key   # KRX Open API 인증키
DART_API_KEY=your_dart_api_key     # DART OpenAPI 인증키

# 내부 경로
DB_PATH=data/quant.db
LOG_PATH=logs/quant.log
LOG_LEVEL=INFO
```

---

## 1-5. .gitignore

```gitignore
venv/
.env
__pycache__/
*.pyc
*.log
data/quant.db
logs/
.DS_Store
.claude/
*.html          # 백테스트 리포트
*.png           # 차트 이미지
notebooks/.ipynb_checkpoints/
```

---

## 1-6. 폴더 구조 초기화

```bash
mkdir -p config data factors strategy backtest trading notify dashboard scheduler tests notebooks logs data
touch config/__init__.py config/settings.py
touch data/__init__.py data/collector.py data/processor.py data/storage.py
touch factors/__init__.py factors/value.py factors/momentum.py factors/quality.py factors/composite.py
touch strategy/__init__.py strategy/screener.py strategy/rebalancer.py
touch backtest/__init__.py backtest/engine.py backtest/metrics.py backtest/report.py
touch trading/__init__.py trading/kiwoom_api.py trading/order.py
touch notify/__init__.py notify/telegram.py
touch dashboard/app.py scheduler/main.py
touch tests/test_factors.py tests/test_backtest.py tests/test_kiwoom_api.py
touch README.md
```

---

## 1-7. Claude Code CLI 활용 팁

```bash
# 프로젝트 루트에서 실행
cd korean-quant
claude

# 자주 쓰는 명령
/help                    # 명령어 목록
/compact                 # 컨텍스트 압축 (긴 대화 시)
/clear                   # 컨텍스트 초기화

# 효과적인 요청 예시
"data/collector.py의 get_universe() 함수를 구현해줘.
 pykrx의 stock.get_market_ticker_list()를 사용하고,
 docs/03_data_pipeline.md의 명세를 따라줘."

"factors/value.py를 작성해줘.
 CLAUDE.md의 밸류 팩터 명세와 PBR(0.5)+PER(0.3)+DIV(0.2) 가중치를 적용해줘."

# 파일 컨텍스트 추가
"@data/collector.py를 보고 @factors/value.py에서 사용 방법 알려줘"
```

### Fast Mode 활성화 (토큰 절약)
```bash
# CLAUDE.md의 내용을 잘 작성해두면
# Claude Code가 컨텍스트를 유지하며 효율적으로 작업
```
