# CLAUDE.md — Korean Multi-Factor Quant System v2.0

> ⚠️ 이 프로젝트는 v2.0 전략 재설계("수술") 진행 중입니다.
> 전체 변경 사항은 `docs/PRD_v2.md`를 참조하세요.
> 수술 작업 순서는 `docs/SURGERY_GUIDE.md`를 참조하세요.

## 프로젝트 개요

한국 주식시장(KOSPI/KOSDAQ) 대상 멀티팩터 퀀트 자동매매 시스템.
**밸류(PBR·PCR·배당) × 모멘텀(12-1M) × 퀄리티(GP/A·EY·F-Score)**
복합 팩터로 종목 선정 → 매월 마지막 영업일 신호 → 다음 영업일 시가 리밸런싱.

## 기술 스택

- **Python 3.14**, 가상환경(venv)
- **데이터**: pykrx + pykrx-openapi (KRX Open API) + DART OpenAPI (multi-tier 폴백)
- **백테스트**: 자체 구현 (pandas 기반, 월별 리밸런싱 + Walk-Forward)
- **성과 분석**: quantstats (HTML 리포트)
- **자동매매**: 키움 REST API
  - 운영: `https://api.kiwoom.com`
  - 모의: `https://mockapi.kiwoom.com`
- **DB**: SQLite + SQLAlchemy
- **스케줄링**: APScheduler
- **알림**: python-telegram-bot v21 (async)
- **대시보드**: Streamlit

## 디렉토리 구조

```
quant-system/
├── CLAUDE.md              ← 이 파일 (프로젝트 루트에 위치해야 함)
├── docs/
│   ├── PRD_v2.md          ← v2.0 전략 명세 (핵심 참조 문서)
│   ├── SURGERY_GUIDE.md   ← 수술 작업 가이드
│   └── 01~13_*.md         ← 기존 상세 가이드 (인프라 계층 참조용)
├── config/
│   ├── settings.py        ← 전역 설정 (YAML 오버라이드 + 프리셋 충돌 감지)
│   ├── config.yaml        ← 전략 파라미터 (4+4 프리셋)
│   └── calendar.py        ← KRX 영업일 유틸리티
├── data/
│   ├── collector.py       ← 멀티소스 데이터 수집 (수술: PCR/GP/A 메서드 추가)
│   ├── dart_client.py     ← DART OpenAPI (수술: 현금흐름·매출총이익 추가)
│   ├── processor.py       ← 전처리
│   └── storage.py         ← SQLite (수술: PCR 컬럼 추가)
├── factors/
│   ├── value.py           ← ★ 수술 대상: PER → PCR
│   ├── momentum.py        ← ★ 수술 대상: 유효 데이터 기준 강화
│   ├── quality.py         ← ★ 수술 대상: ROE → GP/A, F-Score 강화
│   ├── composite.py       ← 부분 수정: 새 프리셋 가중치 반영
│   └── utils.py
├── strategy/
│   ├── screener.py        ← ★ 수술 대상: Reporting Lag 처리 추가
│   ├── rebalancer.py
│   └── market_regime.py   ← 부분 수정: risk_free_rate 동적 참조
├── backtest/
│   ├── engine.py          ← ★ 수술 대상: Walk-Forward 모드, 생존자 편향 폴백
│   ├── metrics.py
│   └── report.py
├── trading/
│   ├── kiwoom_api.py      ← 유지
│   └── order.py           ← 유지
├── notify/telegram.py     ← 유지
├── scheduler/main.py      ← 부분 수정: vol_target 중복 제거
├── dashboard/app.py       ← 미세 조정
├── gui/                   ← 유지 (PyQt 기반 GUI, 수술 범위 밖)
└── tests/                 ← 수술 대상 모듈에 대한 테스트 업데이트
```

## v2.0 핵심 전략 파라미터

```python
# === 팩터 구성 (v2.0 변경) ===
# Value: PBR(50%) + PCR(30%) + DIV(20%)    ← PER 제거, PCR 신규
# Momentum: 12M(60%) + 6M(30%) + 3M(10%)   ← 변경 없음
# Quality: GP/A(40%) + EY(30%) + F-Score(30%) ← ROE→GP/A, F-Score 강화

# === 프리셋 4개 ===
# A(균형):    V=0.35 M=0.40 Q=0.25 | MDD서킷=-25% | vol_target=15%
# B(딥밸류):  V=0.60 M=0.00 Q=0.40 | MDD서킷=-30% | vol_target=18%
# C(모멘텀):  V=0.10 M=0.70 Q=0.20 | MDD서킷=-20% | vol_target=15%
# D(방어):    V=0.35 M=0.20 Q=0.45 | MDD서킷=-15% | vol_target=10%

# === 금액 프리셋 4개 ===
# 소액(~500만):  10종목, 유동성1억
# 중액(1~3천만): 20종목, 유동성2억
# 대액(5천~1억): 25종목, 유동성5억
# 거액(3억~):    30종목, 유동성10억

# === 리스크 관리 (v2.0 — 전 프리셋 활성화) ===
# max_drawdown_pct: 0.15 ~ 0.30 (기존 0.99 비활성화 패턴 제거)
# vol_target: 0.10 ~ 0.18 (기존 0.99 비활성화 패턴 제거)
# trailing_stop_pct: 0.20 ~ 0.30 (C의 0.15는 과도 → 0.20으로 상향)

# === Reporting Lag (v2.0 신규) ===
# 연간 보고서: 결산월 + 3개월 후 사용 가능
# 분기 보고서: 결산월 + 45일 후 사용 가능

# === 거래 비용 ===
COMMISSION = 0.00015   # 0.015%
TAX = 0.0018           # 0.18% (매도만)
SLIPPAGE = 0.001       # 0.10% (금액 프리셋이 상향 가능)

# === 배당 처리 (v2.0 변경) ===
# 백테스트에서 배당 추정 제거 (한국 시장 연 1회 집중 → 월별 균등 배분은 부정확)
# 백테스트 수익률에 배당 미포함 (보수적 추정)
# 실전 운용에서는 키움 API 잔고 조회 시 배당금 자동 반영
```

## 코딩 컨벤션 (필수)

- **타입 힌트** 모든 함수에 필수
- **docstring** Args/Returns 형식
- **에러 처리** `try/except` + `logger.error()`, 절대 무시 금지
- **로깅** `logging.getLogger(__name__)`, `print()` 금지
- **설정값** `config/settings.py`에서만 중앙 관리
- **환경 변수** `.env` 파일만, 코드 하드코딩 절대 금지
- **null 비활성화**: `max_drawdown_pct: null`은 비활성화. `0.99` 패턴 사용 금지

## 현재 수술 진행 상태

- [ ] Phase 1: 팩터 재구축
  - [ ] value.py: PER → PCR 교체
  - [ ] quality.py: ROE → GP/A, F-Score 강화 또는 제거
  - [ ] momentum.py: 유효 데이터 기준 강화 (counts >= lookback × 0.7)
  - [ ] processor.py: PCR 전처리 블록 추가
  - [ ] 팩터 상관관계 검증 (Value-Quality 상관 < 0.5 확인)
- [ ] Phase 2: 스크리너 + 백테스트 개선
  - [ ] screener.py: Reporting Lag 처리
  - [ ] collector.py: 생존자 편향 폴백 강화
  - [ ] engine.py: Walk-Forward 기존 메서드 교체 (신규 아님)
  - [ ] metrics.py: RF_ANNUAL 상수 → 동적 참조
  - [ ] engine.py: 배당 추정 제거 (_estimate_dividend_income @deprecated)
- [ ] Phase 3: 프리셋 정리 (config.yaml 4+4, settings.py 충돌 감지)
- [ ] Phase 4: 통합 테스트
  - [ ] vol_target 중복 제거 (scheduler ↔ engine → market_regime 공통화)
  - [ ] screener 캐시 메모리 제한 (maxsize 24)
  - [ ] 전 기간 백테스트 기준선 확보
  - [ ] Walk-Forward 검증
- [ ] Phase 5: 파라미터 Grid Search + 인접 안정성 검증
- [ ] Phase 6: 프리셋 최종 확정 + 실전 준비

## 알려진 이슈 / 주의사항

- **KRX API 변경 (2025-12-27)**: pykrx 배치 API 차단됨. multi-tier 폴백 사용 중
- **PCR 데이터**: DART 재무제표에서 영업활동현금흐름 확보 필요. 없으면 PSR로 폴백
- **GP/A 데이터**: DART 손익계산서 매출총이익. 없으면 매출액-매출원가로 계산
- **F-Score 강화**: DART 전기 데이터 필요. 확보 불가 시 F-Score 제거하고 GP/A+EY로 재분배
- **배당 추정 제거**: v2.0에서 `_estimate_dividend_income()` 비활성화. 한국형 배당 처리는 미래 과제
- **RF_ANNUAL 고정 문제**: metrics.py에서 모듈 로드 시점 상수 → 동적 참조로 수정 필요
- **vol_target 중복**: scheduler/main.py와 engine.py에 동일 로직 존재 → 공통 함수로 추출
- **screener 캐시 누수**: _factor_cache에 maxsize 미설정 → 24개월분 제한 추가
- **GUI 모듈**: `gui/` 폴더는 현행 유지. 수술 범위 밖. PyQt 기반 데스크톱 인터페이스
- **키움 REST API**: 토큰 응답 필드는 `"token"` (access_token 아님)
- **pandas 2.2+**: `freq="BME"` deprecated → `pd.offsets.BMonthEnd()` 사용
- **프리셋 충돌**: 금액 프리셋은 STRATEGY_ONLY_KEYS를 덮어쓸 수 없음 (settings.py에서 검증)
