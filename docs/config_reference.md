# config.yaml 설정 레퍼런스

> 설정 변경 후 앱을 재시작해야 적용됩니다 (핫리로드 미지원).

## dart_notifier

DART 전자공시시스템에서 보유 종목의 신규 공시를 감지하여 Telegram으로 알림합니다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 공시 알림 전체 on/off |
| `polling_interval_minutes` | int | `5` | 폴링 간격 (분, 1 이상) |
| `market_hours_only` | bool | `true` | `true`면 장중 시간에만 폴링 |
| `market_open` | str | `"09:00"` | 장 시작 시각 (HH:MM) |
| `market_close` | str | `"15:30"` | 장 마감 시각 (HH:MM) |

### dart_notifier.instant_alert

즉시 알림 설정. 해당 유형의 공시가 감지되면 즉시 Telegram으로 발송합니다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 즉시 알림 on/off |
| `categories` | list[str] | 아래 참조 | 즉시 알림 대상 공시 카테고리 |

기본 categories:
```yaml
categories:
  - major_report
  - unfaithful_disclosure
  - fair_disclosure
  - largest_shareholder
  - convertible_bond
  - capital_change
  - merger_split
```

### dart_notifier.daily_summary

일일 요약 설정. 즉시 알림 대상이 아닌 공시를 지정 시각에 한 번에 요약 발송합니다.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `enabled` | bool | `true` | 일일 요약 on/off |
| `send_time` | str | `"17:00"` | 요약 발송 시각 (HH:MM) |

### dart_notifier.api_limit

DART API 호출 한도 모니터링.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `daily_warning_threshold` | int | `8000` | 일일 호출 경고 임계값 (100 이상) |

DART OpenAPI 일일 한도는 10,000건입니다. 임계값 도달 시 WARNING 로그가 출력됩니다.

---

## 카테고리 코드 매핑표

`dart_notifier.instant_alert.categories`에는 아래 별칭 또는 DART `pblntf_detail_ty` 코드를 직접 사용할 수 있습니다.

| 별칭 | pblntf_detail_ty 코드 | 설명 |
|------|----------------------|------|
| `major_report` | B001, B002 | 주요사항보고서, 주요경영사항신고 |
| `unfaithful_disclosure` | E001 | 불성실공시법인지정 |
| `fair_disclosure` | E002 | 공정공시 |
| `largest_shareholder` | B003 | 최대주주변경 |
| `convertible_bond` | G001, G002 | 전환사채/신주인수권부사채 발행 |
| `capital_change` | G003, G004 | 유상증자, 무상증자 |
| `merger_split` | H001, H002, H003 | 합병, 분할, 분할합병 |
| `stock_exchange` | I001, I002 | 주식교환/이전, 자기주식취득/처분 |
| `annual_report` | A001 | 사업보고서 |
| `semi_annual_report` | A002 | 반기보고서 |
| `quarterly_report` | A003 | 분기보고서 |

코드를 직접 사용하는 예:
```yaml
categories:
  - major_report
  - B003          # 최대주주변경 (코드 직접 사용)
  - E003          # 시장조치/안내 (별칭 미정의, 코드만 가능)
```

---

## logging

로그 파일 보관 정책.

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `trading_log_retention_days` | int | `90` | 거래 로그 보관 일수 (1 이상) |
| `system_log_retention_days` | int | `30` | 시스템 로그 보관 일수 (1 이상) |

- 거래 로그: `logs/trading.log` (일별 로테이션, `trading.log.YYYYMMDD`)
- 시스템 로그: `logs/quant.log` (크기 기반 로테이션, 10MB x 5)

---

## 검증 규칙

앱 시작 시 config.yaml을 파싱한 후 아래 검증을 수행합니다.
검증 실패 시 `ValueError`를 발생시키고 **앱 시작이 차단됩니다**.

| 규칙 | 설명 |
|------|------|
| `market_open < market_close` | 장 시작이 마감보다 빨라야 함 |
| `market_open`, `market_close` 형식 | HH:MM (예: "09:00") |
| `categories` 유효성 | 별칭 또는 4자리 코드(영문1+숫자3)만 허용 |
| `polling_interval_minutes >= 1` | 1분 이상 |
| `daily_warning_threshold >= 100` | 100 이상 |
| `send_time` 형식 | HH:MM (예: "17:00") |
| `trading_log_retention_days >= 1` | 1일 이상 |
| `system_log_retention_days >= 1` | 1일 이상 |

---

## 기본값 폴백

`dart_notifier` 또는 `logging` 섹션이 config.yaml에 없어도 기본값으로 정상 동작합니다.
이 경우 시작 로그에 해당 섹션의 기본값이 사용되었음이 표시됩니다.
