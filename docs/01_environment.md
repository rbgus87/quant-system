# 01. 개발 환경 설정

## 1-1. 필수 설치

### Python 3.11
```bash
# Windows: python.org에서 3.11.x 다운로드
# 설치 시 "Add Python to PATH" 반드시 체크

python --version   # Python 3.11.x 확인
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
# 핵심
pandas==2.2.3
numpy==1.26.4
scipy==1.13.1

# 한국 주식 데이터
pykrx==1.0.47
finance-datareader==0.9.52

# 시각화
matplotlib==3.9.2
seaborn==0.13.2
plotly==5.24.1

# 성과 분석
quantstats==0.0.62

# HTTP 요청 (키움 REST API)
requests==2.32.3
websocket-client==1.8.0

# 스케줄링
APScheduler==3.10.4

# 알림 (async 버전)
python-telegram-bot==21.7

# 데이터베이스
sqlalchemy==2.0.36

# 환경 변수
python-dotenv==1.0.1

# 대시보드
streamlit==1.40.2

# 개발/테스트
pytest==8.3.3
black==24.10.0
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

# 내부 경로
DB_PATH=data/quant.db
LOG_PATH=logs/quant.log
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
