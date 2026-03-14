# 한국 주식 멀티팩터 퀀트 자동매매 시스템 — 개발 문서

> **전략**: 2계층 프리셋 시스템 (전략 A~H + 금액 7단계) | KOSPI/KOSDAQ | 월 1회 자동 리밸런싱
> **개발 환경**: Python 3.14 + Claude Code CLI + 키움 REST API

---

## 문서 구성

| 파일 | 내용 |
|------|------|
| **`PRD.md`** | 전략 명세, 시스템 요구사항, KPI 목표 |
| **`CLAUDE.md`** | Claude Code CLI 핵심 컨텍스트 (프로젝트 루트에 복사) |
| `docs/01_environment.md` | 개발 환경 설정, requirements.txt, .env |
| `docs/02_architecture.md` | 전체 데이터 흐름, 프로젝트 구조, config/settings.py |
| `docs/03_data_pipeline.md` | 데이터 소싱 (KRX Open API + DART + pykrx), multi-tier 폴백 |
| `docs/04_factors.md` | ValueFactor, MomentumFactor, QualityFactor, MultiFactorComposite |
| `docs/05_backtest.md` | 백테스트 엔진, 성과 분석, 목표 기준 |
| `docs/06_kiwoom_api.md` | 키움 REST API 공식 확인 사항, 연동 코드, 테스트 순서 |
| `docs/07_automation.md` | 텔레그램, APScheduler, Streamlit 대시보드 |
| `docs/08_checklist.md` | Phase별 개발 체크리스트, 버그 목록 |
| `docs/09_investment_sizing.md` | 투자금별 최적 설정, 파라미터 가이드 |
| `docs/10_factor_analysis.md` | 멀티팩터 구성 분석, 팩터 가중치 상세 |
| `docs/11_mock_trading_test_plan.md` | 모의투자 검증 계획 |
| `docs/12_preset_compatibility.md` | 전략 프리셋(A~H) + 금액 프리셋 궁합 분석 |
| `docs/13_oracle_cloud_deployment.md` | Oracle Cloud Always Free 배포 가이드 |

---

## 빠른 시작

```bash
# 1. 환경 구축
git clone <repo> && cd korean-quant
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # API 키 입력

# 2. Claude Code CLI 실행
claude

# 3. 백테스트 실행
python run_backtest.py

# 4. 스케줄러 실행
python scheduler/main.py

# 5. 대시보드 실행
streamlit run dashboard/app.py
```

---

## 발견된 주요 버그 수정 내역 (원본 가이드 대비)

1. **도메인 오류**: `openapi.kiwoom.com` → 실제 API: `api.kiwoom.com` / `mockapi.kiwoom.com`
2. **토큰 필드**: `access_token` → `token`
3. **스케줄러**: `KISApiClient` 미존재 참조 → `KiwoomRestClient`
4. **스케줄러**: `schedule` 라이브러리 → `APScheduler BlockingScheduler`
5. **pandas 호환**: `freq="BME"` deprecated → `pd.offsets.BMonthEnd()`
6. **pykrx 컬럼**: 한글 컬럼명 rename 로직 추가
7. **모멘텀 수익률**: 하드코딩 `iloc[-22]` → `relativedelta` 정확한 날짜 계산

## KRX API 변경 사항 (2025-12-27)

pykrx 배치 API가 KRX Data Marketplace 로그인 필수화로 전면 차단되었습니다.
현재 multi-tier 데이터 소싱 구조로 대응 완료:
- **1차**: SQLite 캐시 → **2차**: KRX Open API (pykrx-openapi) → **3차**: DART OpenAPI → **4차**: pykrx 개별 폴백

---

## 참고 자료

- 키움 REST API 포털: https://openapi.kiwoom.com
- pykrx: https://github.com/sharebook-kr/pykrx
- quantstats: https://github.com/ranaroussi/quantstats
- Henry Quant 퀀트 쿡북: https://hyunyulhenry.github.io/quant_cookbook/

> ⚠️ 이 문서는 교육 목적입니다. 실제 투자 손실에 대한 책임은 사용자에게 있습니다.
