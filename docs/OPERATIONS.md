# OPERATIONS.md — 운영 가이드

> 일상 운영/점검 절차 모음. 실전 계좌를 다루기 전에 반드시 정독.

---

## 1. 자동화 스케줄 전체

| 시각 | Job | 대상 | 비고 |
|------|-----|------|------|
| 08:50 | `run_scheduled_rebalancing` | 분기 마지막 영업일 (3/6/9/12월) | 스크리닝 + 주문, 09:00 시가 체결 |
| 09:00-15:00 (30분) | `run_risk_guard_check` | 장중 리스크 감시 | 알림 전용 |
| 09:30 | `run_risk_guard_delisting` | 관리종목 캐시 갱신 | 하루 1회 |
| 09:00-15:30 (5분) | `run_dart_disclosure_poll` | DART 공시 즉시 알림 | `dart_notifier.enabled` 조건 |
| 15:15 | `run_daily_defense_check` | MDD 서킷브레이커 + 트레일링 스톱 | 현재 프리셋에선 null(OFF) |
| 15:35 | `run_daily_report` | 일간 리포트 + 스냅샷 저장 | 텔레그램 발송 |
| **16:00** | **`run_daily_data_collection`** | **일별 데이터 수집 (신규)** | **KOSPI prefetch + fundamentals** |
| 17:00 | `run_dart_daily_summary` | DART 일일 공시 요약 | 텔레그램 발송 |

---

## 2. 일별 데이터 수집 (16:00)

### 2-1. 동작 개요

매 영업일 16:00 (장 마감 15:30 후 30분)에 `scheduler/main.py::run_daily_data_collection`가
아래 순서로 실행된다.

1. `is_business_day()` 확인 — 휴장일이면 즉시 종료.
2. `collector.prefetch_daily_trade(today, "KOSPI")`
   → `daily_price` + `market_cap` 테이블 갱신 (KRX Open API 1회 호출로 ~950종목).
3. `collector.get_fundamentals_all(today, "KOSPI")`
   → `fundamental` 테이블 갱신 (KRX API PER/PBR → DART 폴백).
4. 실패 시 30분 후 1회 재시도 (DART API 일시 장애 대응).
5. 최종 실패 시 텔레그램 에러 알림.

### 2-2. 수동 실행

```bash
# 스케줄러를 띄우지 않고 1회만 실행
python scheduler/main.py --collect-now

# 특정 날짜나 기간을 수집할 때는 백필 스크립트 사용 (다음 섹션)
```

### 2-3. 설정 (config.yaml)

```yaml
schedule:
  daily_data_collection:
    enabled: true
    hour: 16
    minute: 0
    markets: ["KOSPI"]        # 향후 ["KOSPI", "KOSDAQ"] 확장 가능
```

> ※ 설정 변경 시 스케줄러 프로세스 재시작 필요 (핫리로드 미지원).

---

## 3. 공백 백필 (`scripts/backfill_data.py`)

### 3-1. 언제 쓰나

- 스케줄러가 꺼져 있던 기간이 있을 때
- 수집 Job이 실패했는데 재시도에서도 실패한 경우
- 프로젝트를 처음 세팅해 과거 데이터를 일괄 로드할 때

### 3-2. 사용법

```bash
# 1) 지정 기간을 통째로 수집
python scripts/backfill_data.py --start 20260331 --end 20260413

# 2) KOSDAQ 포함
python scripts/backfill_data.py --start 20260101 --end 20260414 --market ALL

# 3) DB 누락 자동 감지 (테이블별 ticker 수 < 500 이면 누락으로 간주)
python scripts/backfill_data.py --missing-only --start 20260101 --end 20260414

# 4) 실패 로그로부터 재시도
python scripts/backfill_data.py --retry-failed logs/backfill_failed_20260414.txt
```

### 3-3. 실패 처리

- 실패한 날짜는 `logs/backfill_failed_YYYYMMDD.txt`에 한 줄씩 저장됨.
- `--retry-failed` 옵션으로 바로 재실행 가능.
- 같은 날짜가 반복 실패하면:
  - KRX Open API 일 10,000건 한도 초과 여부 확인 (`.env`의 `KRX_OPENAPI_KEY`)
  - DART API 키 만료/한도 확인 (`DART_API_KEY`)
  - 네트워크 혹은 API 자체 장애 → 다음 날 재시도

---

## 4. 리밸런싱 D-3 점검 체크리스트

분기 리밸런싱(3/6/9/12월 마지막 영업일)은 연중 4회뿐이다. 신호가 틀리면 3개월을 손해보므로
**D-3 ~ D-1**에 아래 4가지를 반드시 확인한다.

### 4-1. 데이터 최신성

```bash
python -c "
from data.storage import DataStorage
from sqlalchemy import text
s = DataStorage()
with s.engine.connect() as conn:
    for tbl in ['daily_price', 'market_cap', 'fundamental']:
        r = conn.execute(text(f'SELECT MAX(date) FROM {tbl}')).fetchone()
        print(f'{tbl:15s}: 최신 {r[0]}')
"
```

- 기대: 모두 `D-1 영업일` 또는 `D일`.
- 공백 발견 시 즉시 백필:
  ```bash
  python scripts/backfill_data.py --missing-only --start <공백_시작일> --end <오늘>
  ```

### 4-2. 스케줄러 프로세스 정상 동작

- `scheduler/main.py`가 백그라운드에서 돌고 있는지 (작업 관리자 / `tasklist`).
- 최근 일별 리포트가 매일 15:35에 텔레그램으로 왔는지.
- 16:00 데이터 수집 실패 알림이 없었는지.

### 4-3. API 키 / 텔레그램 생존

```bash
python scripts/selftest.py
```

- 1~4단계(정적분석·단위테스트·통합스모크·exe번들) 모두 PASS 확인.

### 4-4. 스크리닝 드라이런

```bash
python scheduler/main.py --screen-only
```

- 상위 20종목 로그가 정상 출력되는지.
- 로그에 `복합 스코어 계산 결과 없음` / `필터 후 유효 종목 없음` 경고가 없는지.

---

## 5. 장애 대응

### 5-1. 리밸런싱 당일 스크리닝 0건

증상: 08:50 리밸런싱에서 "스크리닝 결과가 비어 있어 리밸런싱을 건너뜁니다" 메시지.

원인 후보:
1. 데이터 공백 (`daily_price.MAX(date)` < D-1)
2. KRX/DART API 장애
3. F-Score 필터 너무 빡빡해서 통과 종목 없음

조치:
- 즉시 백필 → `python scripts/backfill_data.py --missing-only`
- 공백이 해소되면 수동 리밸런싱: `python scheduler/main.py --now`

### 5-2. 16:00 수집 최종 실패 알림

- 1회 재시도 이후에도 실패 → 텔레그램에 에러 메시지.
- 같은 날 다시 시도: `python scheduler/main.py --collect-now`
- 여전히 실패면 원인 파악 후 다음 날 수집 + 백필로 보충.

### 5-3. 관리종목 지정 경고 수신

- `monitor/risk_guard.py`가 감지, 자동 매도는 없음 (**알림 전용**).
- 운용자 판단으로 수동 매도 또는 다음 분기까지 보유.

---

## 6. 운영 팁

- **DB 백업**: 리밸런싱 직전 `DataStorage.backup()` 자동 호출됨. 수동 백업은
  `cp data/quant.db data/quant.db.YYYYMMDD`.
- **로그 위치**: `logs/quant.log` (system), `logs/trading.log` (거래).
  보관 기간은 `config.yaml::logging` 섹션.
- **프리셋 변경 후**: 스크리너 인메모리 캐시(`MultiFactorScreener._factor_cache`)는
  캐시 키에 팩터 가중치를 포함하므로 프리셋 간 교차 오염은 없음. 단, 프로세스
  재시작으로 캐시를 비우는 것이 깔끔.
