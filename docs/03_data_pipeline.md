# 03. 데이터 수집 파이프라인

## 3-0. KRX API 변경 사항 (2025-12-27)

> **중요**: 2025-12-27부터 KRX Data Marketplace가 로그인 필수로 전환되어
> pykrx의 배치 API (fundamental, market cap, ticker list)가 전부 차단되었습니다.

**차단된 API** (절대 사용 금지):
- `stock.get_market_ohlcv(date, market="KOSPI")` — 전종목 배치 OHLCV
- `stock.get_market_fundamental(date, date, market)` — 전종목 배치 기본지표
- `stock.get_market_cap(date, market)` — 전종목 배치 시가총액

**여전히 작동하는 API**:
- `stock.get_market_ohlcv(start, end, ticker)` — 개별 종목 OHLCV (Naver 기반)

**현재 데이터 소싱 구조** (multi-tier 폴백):
```
SQLite 캐시 → KRX Open API (pykrx-openapi) → DART OpenAPI → pykrx 개별 폴백
```

| 데이터 유형 | 1차 소스 | 2차 소스 | 3차 소스 |
|------------|---------|---------|---------|
| 유니버스 (종목 목록) | KRX Open API | pykrx | - |
| Fundamentals (PER/PBR/EPS/BPS/DIV) | KRX Open API | DART (재무제표 → PER/PBR 계산) | pykrx 폴백 |
| 시가총액 | KRX Open API | pykrx 폴백 | - |
| OHLCV (일봉) | SQLite 캐시 | pykrx 개별 종목 조회 | - |
| 배당 (DPS) | DART 알롯매터 API | - | - |

> 환경변수: `KRX_OPENAPI_KEY` (KRX Open API), `DART_API_KEY` (DART OpenAPI)

---

## 3-1. pykrx 핵심 API 정리

> ⚠️ **pykrx 응답 컬럼명은 한글** — 수집 후 반드시 영문으로 rename 필요
> ⚠️ 배치 API는 KRX 로그인 필수화로 차단됨 — 개별 종목 API만 사용 가능

| 함수 | 설명 | 상태 |
|------|------|------|
| `stock.get_market_ticker_list(date, market)` | 시장 전체 종목 코드 | **차단됨** → KRX Open API 사용 |
| `stock.get_market_ticker_name(ticker)` | 종목명 | 작동 |
| `stock.get_market_ohlcv(start, end, ticker)` | 단일 종목 일봉 | **작동** (Naver 기반) |
| `stock.get_market_ohlcv(date, date, market)` | 전체 시장 OHLCV 배치 | **차단됨** → KRX Open API 사용 |
| `stock.get_market_fundamental(date, date, market)` | 전체 시장 기본 지표 배치 | **차단됨** → KRX Open API + DART 사용 |
| `stock.get_market_cap(date, market)` | 시가총액 | **차단됨** → KRX Open API 사용 |

---

## 3-2. data/collector.py

```python
# data/collector.py
import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# pykrx OHLCV 컬럼 매핑 (한글 → 영문)
OHLCV_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
}

# pykrx Fundamental 컬럼 매핑
FUNDAMENTAL_COLUMNS = {
    "BPS": "BPS",
    "PER": "PER",
    "PBR": "PBR",
    "EPS": "EPS",
    "DIV": "DIV",
}


class KRXDataCollector:
    """KRX 데이터 수집 (pykrx 기반)"""

    def __init__(self, request_delay: float = 0.5):
        """
        Args:
            request_delay: API 호출 간격 (초). 과호출 방지 필수.
        """
        self.delay = request_delay

    # ───────────────────────────────────────────────
    # 유니버스
    # ───────────────────────────────────────────────

    def get_universe(self, date: str, market: str = "KOSPI") -> pd.DataFrame:
        """
        특정 날짜 기준 상장 종목 목록 조회 (생존 편향 방지)

        Args:
            date: 기준 날짜 (YYYYMMDD) — 해당 날짜의 상장 종목만 반환
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(columns=[ticker, name, market])
        """
        try:
            tickers = stock.get_market_ticker_list(date, market=market)
            logger.info(f"[{date}] {market} 종목 수: {len(tickers)}")

            rows = []
            for i, ticker in enumerate(tickers):
                try:
                    name = stock.get_market_ticker_name(ticker)
                    rows.append({"ticker": ticker, "name": name, "market": market})
                    if (i + 1) % 100 == 0:
                        logger.info(f"  진행: {i+1}/{len(tickers)}")
                        time.sleep(self.delay)
                except Exception as e:
                    logger.warning(f"  종목명 조회 실패 ({ticker}): {e}")

            return pd.DataFrame(rows)

        except Exception as e:
            logger.error(f"유니버스 조회 실패: {e}")
            raise

    # ───────────────────────────────────────────────
    # OHLCV
    # ───────────────────────────────────────────────

    def get_ohlcv(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        단일 종목 일봉 OHLCV 조회

        Returns:
            DataFrame(index=date, columns=[open, high, low, close, volume])
        """
        try:
            df = stock.get_market_ohlcv(start_date, end_date, ticker)
            if df.empty:
                return pd.DataFrame()
            # 한글 컬럼 → 영문 rename
            df = df.rename(columns=OHLCV_COLUMNS)
            df.index.name = "date"
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as e:
            logger.warning(f"OHLCV 조회 실패 ({ticker}): {e}")
            return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 기본 지표 (Fundamental)
    # ───────────────────────────────────────────────

    def get_fundamentals_all(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """
        전체 시장 기본 지표 일괄 조회 (배치 — 개별 호출보다 훨씬 빠름)

        pykrx: stock.get_market_fundamental(date, date, market)
        → 전체 종목 PBR, PER, EPS, BPS, DIV 한 번에 반환

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
        """
        try:
            df = stock.get_market_fundamental(date, date, market)
            if df.empty:
                logger.warning(f"[{date}] 기본 지표 데이터 없음")
                return pd.DataFrame()
            df.index.name = "ticker"
            # 컬럼이 한글일 경우 rename (pykrx 버전에 따라 다름)
            df = df.rename(columns=FUNDAMENTAL_COLUMNS)
            return df[["BPS", "PER", "PBR", "EPS", "DIV"]]
        except Exception as e:
            logger.error(f"기본 지표 배치 조회 실패 ({date}): {e}")
            return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 시가총액
    # ───────────────────────────────────────────────

    def get_market_cap(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """
        전체 시장 시가총액 조회

        Returns:
            DataFrame(index=ticker, columns=[market_cap, shares])
        """
        try:
            df = stock.get_market_cap(date, market=market)
            if df.empty:
                return pd.DataFrame()
            df.index.name = "ticker"
            # 한글 컬럼명 처리
            rename_map = {"시가총액": "market_cap", "상장주식수": "shares"}
            df = df.rename(columns=rename_map)
            cols = [c for c in ["market_cap", "shares"] if c in df.columns]
            return df[cols]
        except Exception as e:
            logger.error(f"시가총액 조회 실패 ({date}): {e}")
            return pd.DataFrame()


class ReturnCalculator:
    """수익률 계산기 (모멘텀 팩터용)"""

    def __init__(self):
        self.collector = KRXDataCollector(request_delay=0.3)

    def get_momentum_return(
        self,
        ticker: str,
        base_date: str,
        lookback_months: int = 12,
        skip_months: int = 1,
    ) -> Optional[float]:
        """
        모멘텀 수익률 계산: (base_date - skip_months) 가격 / (base_date - lookback_months) 가격 - 1

        표준 12개월 모멘텀: lookback_months=12, skip_months=1
        → (t-1개월 가격) / (t-12개월 가격) - 1  (최근 1개월 반전 효과 제거)

        Args:
            ticker: 종목코드
            base_date: 기준 날짜 (YYYYMMDD)
            lookback_months: 되돌아볼 기간 (월)
            skip_months: 최근 제외 기간 (월, 반전 효과 제거)

        Returns:
            float 수익률 또는 None (데이터 부족)
        """
        base_dt = datetime.strptime(base_date, "%Y%m%d")
        end_dt = base_dt - relativedelta(months=skip_months)      # t-1개월
        start_dt = base_dt - relativedelta(months=lookback_months) # t-12개월

        end_str = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")

        df = self.collector.get_ohlcv(ticker, start_str, end_str)
        if df is None or len(df) < 10:
            return None

        start_price = df["close"].iloc[0]   # t-12개월 가격
        end_price = df["close"].iloc[-1]    # t-1개월 가격

        if start_price <= 0:
            return None

        return float(end_price / start_price - 1)

    def get_returns_for_universe(
        self,
        tickers: list[str],
        base_date: str,
        lookback_months: int = 12,
        skip_months: int = 1,
    ) -> pd.Series:
        """
        유니버스 전체 종목 모멘텀 수익률 계산

        Returns:
            Series(index=ticker, values=return_rate)
        """
        results = {}
        total = len(tickers)

        for i, ticker in enumerate(tickers):
            ret = self.get_momentum_return(ticker, base_date, lookback_months, skip_months)
            if ret is not None:
                results[ticker] = ret

            if (i + 1) % 50 == 0:
                logger.info(f"  수익률 계산 중: {i+1}/{total}")
                time.sleep(0.3)

        logger.info(f"수익률 계산 완료: {len(results)}/{total}개")
        return pd.Series(results, name=f"return_{lookback_months}m")
```

---

## 3-3. data/processor.py

```python
# data/processor.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class DataProcessor:
    """데이터 전처리 및 이상치 처리"""

    @staticmethod
    def clean_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
        """
        기본 지표 이상치 제거 및 정제

        처리 내용:
        - PBR, PER: 0 이하 제거 (의미 없음)
        - DIV: 음수 제거
        - EPS, BPS: 결측치 허용 (ROE 계산에만 사용)
        - 각 컬럼 상위 1% Winsorize (이상치 클리핑)
        """
        cleaned = df.copy()

        for col in ["PBR", "PER"]:
            if col in cleaned.columns:
                cleaned[col] = cleaned[col].where(cleaned[col] > 0, np.nan)
                upper = cleaned[col].quantile(0.99)
                cleaned[col] = cleaned[col].clip(upper=upper)

        if "DIV" in cleaned.columns:
            cleaned["DIV"] = cleaned["DIV"].where(cleaned["DIV"] >= 0, np.nan)

        if "BPS" in cleaned.columns:
            # 자본잠식(BPS <= 0) 종목의 BPS를 NaN으로 처리
            cleaned["BPS"] = cleaned["BPS"].where(cleaned["BPS"] > 0, np.nan)

        logger.info(f"전처리 후 유효 종목: {cleaned.dropna(how='all').shape[0]}")
        return cleaned

    @staticmethod
    def filter_universe(
        tickers: list[str],
        market_cap: pd.DataFrame,
        fundamentals: pd.DataFrame,
        min_cap_percentile: float = 10.0,
        finance_tickers: list[str] = None,
    ) -> list[str]:
        """
        유니버스 필터 적용

        Args:
            min_cap_percentile: 시가총액 하위 N% 제외
            finance_tickers: 금융주 종목 코드 리스트

        Returns:
            필터링된 종목 코드 리스트
        """
        valid = set(tickers)

        # 시가총액 필터
        if not market_cap.empty and "market_cap" in market_cap.columns:
            threshold = market_cap["market_cap"].quantile(min_cap_percentile / 100)
            large_caps = set(market_cap[market_cap["market_cap"] >= threshold].index)
            before = len(valid)
            valid &= large_caps
            logger.info(f"시가총액 필터: {before} → {len(valid)}")

        # 금융주 제외
        if finance_tickers:
            before = len(valid)
            valid -= set(finance_tickers)
            logger.info(f"금융주 제외: {before} → {len(valid)}")

        return list(valid)
```

---

## 3-4. 데이터 검증 (Jupyter에서 실행)

```python
# notebooks/01_data_exploration.ipynb

from pykrx import stock
import pandas as pd

# 1. 유니버스 확인
date = "20240101"
tickers = stock.get_market_ticker_list(date, market="KOSPI")
print(f"KOSPI 종목 수: {len(tickers)}")  # 약 800~900개

# 2. 기본 지표 배치 조회
fund = stock.get_market_fundamental(date, date, "KOSPI")
print(fund.head())
print(fund.columns.tolist())  # ['BPS', 'PER', 'PBR', 'EPS', 'DIV'] 확인

# 3. 시가총액 분포
mktcap = stock.get_market_cap(date, market="KOSPI")
print(mktcap["시가총액"].describe())

# 4. 삼성전자 OHLCV 테스트
ohlcv = stock.get_market_ohlcv("20240101", "20240131", "005930")
print(ohlcv.head())
print(ohlcv.columns.tolist())  # 한글 컬럼 확인
```

---

## 3-5. data/dart_client.py (추가됨)

DART OpenAPI를 통해 재무제표 기반 PER/PBR/EPS/BPS/DIV를 계산합니다.
KRX Open API에서 누락된 Fundamental 데이터를 보완하는 2차 소스로 사용됩니다.

주요 기능:
- `DartClient.get_fundamentals_for_date(tickers, date_str, close_prices, shares)` — DART + KRX 가격 결합하여 PER/PBR 계산
- `DartClient.get_dps_for_tickers(tickers, bsns_year, reprt_code)` — 주당배당금 조회 (alotMatter API)
- 보고서 기간 자동 결정: 날짜 기준 연간/반기/분기 보고서 선택 (선견 편향 방지)
- 법인코드 매핑: ticker ↔ DART corp_code (7일 파일 캐시)
- DPS JSON 캐시: 연도별 배당 데이터 캐시

---

## 3-6. 주의사항

| 항목 | 내용 |
|------|------|
| **KRX 배치 API 차단** | 2025-12-27부터 pykrx 배치 API 전면 차단 → KRX Open API + DART 사용 |
| pykrx 개별 조회 | `get_market_ohlcv(start, end, ticker)`는 Naver 기반으로 여전히 작동 |
| KRX Open API 제한 | 일 10,000건 호출 제한 (`KRX_OPENAPI_KEY` 환경변수 필요) |
| DART API 제한 | 일 10,000건 호출 제한 (`DART_API_KEY` 환경변수 필요) |
| 결측치 처리 | PBR/PER이 없는 종목은 해당 팩터 스코어에서만 제외, 다른 팩터는 유지 (NaN-aware 가중합) |
| 컬럼명 | pykrx 버전에 따라 한글/영문 다를 수 있음 → `print(df.columns)` 확인 후 작업 |
| 생존 편향 | 과거 날짜 기준 종목 조회로 당시 상장 종목만 반환 → 자동 방지됨 |
