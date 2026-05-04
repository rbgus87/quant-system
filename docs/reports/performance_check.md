# 성능 진단 리포트 (BEFORE)
> 측정 시각: 2026-05-04 16:48:25
> DB: data/quant.db (1119.6 MB)

## 1. DB 성능 진단

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| DB 디스크 크기 | 1,119.6 MB | 🟢 | - |
| 테이블 행수 | daily_price=5,960,416, fundamental=174,264, market_cap=3,890,272, factor_score=34,572, portfolio=55, trade=20, delisted_stock=1,928 | 🟢 | - |
| 복합 인덱스 누락 | fundamental(date, market), market_cap(date, market) | 🔴 | data/storage.py에 _migrate_compound_indexes 추가 |
| 총 인덱스 수 | 12개 (테이블 7개) | 🟢 | - |
| 쿼리 [fundamental WHERE date+market] | 4.9ms (행 835, plan: SEARCH fundamental USING INDEX ix_fundamental_date (date=?)) | 🟢 | - |
| 쿼리 [market_cap WHERE date+market] | 2.3ms (행 949, plan: SEARCH market_cap USING INDEX ix_market_cap_date (date=?)) | 🟢 | - |
| 쿼리 [daily_price WHERE ticker+date>=] | 6.7ms (행 205, plan: SEARCH daily_price USING INDEX sqlite_autoindex_daily_price_1 (ticker=? AND date) | 🟢 | - |
| 쿼리 [daily_price WHERE date (전종목)] | 3.1ms (행 949, plan: SEARCH daily_price USING INDEX ix_daily_price_date (date=?)) | 🟢 | - |

## 2. 객체 생성 패턴

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| DataStorage 생성 호출 수 (scheduler+order) | 5회 | 🔴 | 모듈 레벨 싱글턴화 권장 (get_storage 함수) |
| KiwoomRestClient 생성 호출 수 | 6회 | 🔴 | 모듈 레벨 싱글턴화 권장 (get_api 함수) |
| KRXDataCollector 생성 호출 수 | 4회 | 🔴 | 모듈 레벨 싱글턴화 권장 (get_collector 함수) |
| 단일 인스턴스 생성 시간 | DataStorage=4.5ms, KiwoomRestClient=0.0ms, KRXDataCollector=4.9ms | 🟢 | 리밸런싱 1회당 누적 약 9ms 절감 가능 |
| DataStorage.__init__ 부수효과 | create_engine + 7개 마이그레이션 + Base.metadata.create_all + WAL pragma | 🟡 | - |
| KiwoomRestClient.__init__ 부수효과 | 토큰 미리 발급 안 함 (lazy) — 가벼움 | 🟢 | - |
| KRXDataCollector.__init__ 부수효과 | DataStorage() 생성 (체인) + 캐시 dict 초기화 | 🟢 | - |

## 3. 메모리 사용 패턴

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| screener._factor_cache 최대 크기 | 24개 | 🟢 | 현재 적정 |
| 캐시 키 settings 포함 (오염 방지) | factor_weights=True, quality 옵션=True | 🟢 | - |
| DB 전종목·전기간 로드 시 추정 메모리 | 전체 577MB / 1년치 48MB | 🟡 | 벌크 조회 시 chunk_size + 청크 단위 처리 검토 |
| 변동성 필터 pivot_table 사용 | 사용 중 (벡터화) | 🟢 | - |

## 4. 코드 효율성

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| pd.concat 루프 내 누적 (O(n²)) | 0건  | 🟢 | - |
| N+1 쿼리 (개별 종목 루프 폴백) | 0건 (벌크 실패 시 폴백 — 의도적) | 🟢 | 벌크 우선 + 미스만 개별 폴백 — 현 패턴 OK |
| WHERE 절 사용 (전체 로드 회피) | WHERE 사용 8회 / SELECT * 0회 | 🟢 | - |
| GUI 자동 갱신 주기 (장 마감 후 동작) | 장 마감 후에도 30초 간격 | 🟡 | 장외 5분 간격으로 확대 |

## 5. 스케줄러 효율성

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| scheduler/main.py 규모 | 1331줄 / 25함수 / 12 Job | 🟢 | - |
| time.sleep() 사용 (블로킹) | 1회 | 🟡 | 장기 대기는 APScheduler date trigger 권장 |
| 스케줄러 타입 | BlockingScheduler (단일 스레드) | 🟡 | 대부분 IO bound라서 OK; 동시성 필요 시 max_workers 조정 |
| DB engine.dispose() 종료 정리 | 호출 안 함 | 🟡 | 종료 시 engine.dispose() 추가 권장 |

## 핵심 측정값 (Raw Metrics)

```
  fundamental WHERE date+market: 4.91ms (rows=835)
  market_cap WHERE date+market: 2.3ms (rows=949)
  daily_price WHERE ticker+date>=: 6.68ms (rows=205)
  daily_price WHERE date (전종목): 3.1ms (rows=949)
  DataStorage() 호출 수: 5
  KiwoomRestClient() 호출 수: 6
  KRXDataCollector() 호출 수: 4
  DataStorage __init__: 4.52ms
  KiwoomRestClient __init__: 0.02ms
  KRXDataCollector __init__: 4.87ms
```


---

# 성능 진단 리포트 (AFTER)
> 측정 시각: 2026-05-04 16:48:25
> DB: data/quant.db (1220.3 MB)

## 1. DB 성능 진단

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| DB 디스크 크기 | 1,220.3 MB | 🟢 | - |
| 테이블 행수 | daily_price=5,960,416, fundamental=174,264, market_cap=3,890,272, factor_score=34,572, portfolio=55, trade=20, delisted_stock=1,928 | 🟢 | - |
| 복합 인덱스 누락 | 없음 | 🟢 | - |
| 총 인덱스 수 | 16개 (테이블 7개) | 🟢 | - |
| 쿼리 [fundamental WHERE date+market] | 4.2ms (행 835, plan: SEARCH fundamental USING INDEX ix_fundamental_date_market (date=? AND market=?)) | 🟢 | - |
| 쿼리 [market_cap WHERE date+market] | 2.3ms (행 949, plan: SEARCH market_cap USING INDEX ix_market_cap_date_market (date=? AND market=?)) | 🟢 | - |
| 쿼리 [daily_price WHERE ticker+date>=] | 1.0ms (행 205, plan: SEARCH daily_price USING INDEX sqlite_autoindex_daily_price_1 (ticker=? AND date) | 🟢 | - |
| 쿼리 [daily_price WHERE date (전종목)] | 3.0ms (행 949, plan: SEARCH daily_price USING INDEX ix_daily_price_date (date=?)) | 🟢 | - |

## 2. 객체 생성 패턴

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| DataStorage 생성 호출 수 (scheduler+order) | 3회 | 🔴 | 모듈 레벨 싱글턴화 권장 (get_storage 함수) |
| KiwoomRestClient 생성 호출 수 | 2회 | 🟢 | - |
| KRXDataCollector 생성 호출 수 | 1회 | 🟢 | - |
| 단일 인스턴스 생성 시간 | DataStorage=5.9ms, KiwoomRestClient=0.0ms, KRXDataCollector=5.8ms | 🟢 | - |
| DataStorage.__init__ 부수효과 | create_engine + 7개 마이그레이션 + Base.metadata.create_all + WAL pragma | 🟡 | - |
| KiwoomRestClient.__init__ 부수효과 | 토큰 미리 발급 안 함 (lazy) — 가벼움 | 🟢 | - |
| KRXDataCollector.__init__ 부수효과 | DataStorage() 생성 (체인) + 캐시 dict 초기화 | 🟢 | - |

## 3. 메모리 사용 패턴

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| screener._factor_cache 최대 크기 | 24개 | 🟢 | 현재 적정 |
| 캐시 키 settings 포함 (오염 방지) | factor_weights=True, quality 옵션=True | 🟢 | - |
| DB 전종목·전기간 로드 시 추정 메모리 | 전체 577MB / 1년치 48MB | 🟡 | 벌크 조회 시 chunk_size + 청크 단위 처리 검토 |
| 변동성 필터 pivot_table 사용 | 사용 중 (벡터화) | 🟢 | - |

## 4. 코드 효율성

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| pd.concat 루프 내 누적 (O(n²)) | 0건  | 🟢 | - |
| N+1 쿼리 (개별 종목 루프 폴백) | 0건 (벌크 실패 시 폴백 — 의도적) | 🟢 | 벌크 우선 + 미스만 개별 폴백 — 현 패턴 OK |
| WHERE 절 사용 (전체 로드 회피) | WHERE 사용 13회 / SELECT * 0회 | 🟢 | - |
| GUI 자동 갱신 주기 (장 마감 후 동작) | 시간대 인지 갱신 | 🟢 | - |

## 5. 스케줄러 효율성

| 항목 | 현재 상태 | 심각도 | 개선 방안 |
|------|-----------|:------:|-----------|
| scheduler/main.py 규모 | 1380줄 / 29함수 / 12 Job | 🟢 | - |
| time.sleep() 사용 (블로킹) | 1회 | 🟡 | 장기 대기는 APScheduler date trigger 권장 |
| 스케줄러 타입 | BlockingScheduler (단일 스레드) | 🟡 | 대부분 IO bound라서 OK; 동시성 필요 시 max_workers 조정 |
| DB engine.dispose() 종료 정리 | 호출 | 🟢 | - |

## 핵심 측정값 (Raw Metrics)

```
  fundamental WHERE date+market: 4.21ms (rows=835)
  market_cap WHERE date+market: 2.33ms (rows=949)
  daily_price WHERE ticker+date>=: 1.01ms (rows=205)
  daily_price WHERE date (전종목): 3.02ms (rows=949)
  DataStorage() 호출 수: 3
  KiwoomRestClient() 호출 수: 2
  KRXDataCollector() 호출 수: 1
  DataStorage __init__: 5.89ms
  KiwoomRestClient __init__: 0.02ms
  KRXDataCollector __init__: 5.75ms
```


---


## Before vs After 비교

| 항목 | Before | After | 개선 |
|------|--------|-------|------|
| 쿼리 [fundamental WHERE date+market] | 4.9ms | 4.2ms | 1.2배 빠름 |
| 쿼리 [market_cap WHERE date+market] | 2.3ms | 2.3ms | 동등 (0.99x) |
| 쿼리 [daily_price WHERE ticker+date>=] | 6.7ms | 1.0ms | 6.6배 빠름 |
| 쿼리 [daily_price WHERE date (전종목)] | 3.1ms | 3.0ms | 동등 (1.03x) |
| DataStorage() 호출 수 | 5회 | 3회 | -2회 |
| KiwoomRestClient() 호출 수 | 6회 | 2회 | -4회 |
| KRXDataCollector() 호출 수 | 4회 | 1회 | -3회 |
| DB 인덱스 총 수 | 12개 | 16개 | +4개 |