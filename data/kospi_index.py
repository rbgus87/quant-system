# data/kospi_index.py
"""KOSPI 종합지수(코드 1001) 시계열 조회/캐시.

market_regime 200일 이동평균 + monitor.benchmark 동일 소스로 통일하기 위한 모듈.

데이터 소스 우선순위:
  1. DB 캐시 (daily_price 테이블, ticker='KOSPI', market='INDEX')
  2. Naver Finance 페이지네이션 (KRX 로그인 전환 이후 가장 안정적인 시계열 소스)
  3. KRX Open API get_kospi_daily_trade (단일 날짜만, 일별 갱신용 보조)

설계 원칙:
  - daily_price 스키마 재사용 (마이그레이션 불필요, ticker='KOSPI'/market='INDEX'로 분리)
  - Naver는 종가만 제공하므로 OHLV는 close 동일값으로, volume은 0으로 채운다
    (market_regime은 close만 사용하므로 충분)
"""
from __future__ import annotations

import logging
import re
import urllib.request
from datetime import date, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# DB 캐시 식별자
KOSPI_INDEX_TICKER = "KOSPI"
KOSPI_INDEX_MARKET = "INDEX"

# Naver Finance 시세 일자별 페이지
_NAVER_BASE = "https://finance.naver.com/sise/sise_index_day.naver?code=KOSPI"
_NAVER_ROW_RE = re.compile(
    r'<td class="date">([^<]+)</td>[\s\S]*?<td class="number_1">([^<]+)</td>'
)
_NAVER_MAX_PAGES = 60  # 약 360영업일 (페이지당 6일) — 1.5년치 백필 가능


def _fetch_naver_page(page: int) -> list[tuple[date, float]]:
    """Naver 페이지 1장에서 (date, close) 리스트를 추출한다."""
    url = f"{_NAVER_BASE}&page={page}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("euc-kr", errors="ignore")
    except Exception as e:
        logger.warning("Naver KOSPI 페이지 %d 조회 실패: %s", page, e)
        return []

    rows = _NAVER_ROW_RE.findall(html)
    out: list[tuple[date, float]] = []
    for date_raw, close_raw in rows:
        try:
            d = datetime.strptime(date_raw.strip(), "%Y.%m.%d").date()
            close = float(close_raw.strip().replace(",", ""))
            out.append((d, close))
        except ValueError:
            continue
    return out


def _fetch_naver_kospi_index_series(
    start_date: date, end_date: date
) -> pd.DataFrame:
    """Naver 페이지네이션으로 KOSPI 종합지수 시계열을 수집한다.

    Args:
        start_date: 시작일 (포함)
        end_date: 종료일 (포함)

    Returns:
        DataFrame(index=date, columns=[open, high, low, close, volume])
        Naver는 종가만 제공하므로 OHLV는 close 동일값, volume은 0.
        실패 시 빈 DataFrame.
    """
    collected: dict[date, float] = {}
    for page in range(1, _NAVER_MAX_PAGES + 1):
        rows = _fetch_naver_page(page)
        if not rows:
            break
        for d, close in rows:
            if d > end_date:
                continue
            collected[d] = close
        # 페이지의 가장 오래된 날짜가 시작일 이전이면 중단
        oldest = min(d for d, _ in rows)
        if oldest <= start_date:
            break

    filtered = {d: c for d, c in collected.items() if start_date <= d <= end_date}
    if not filtered:
        return pd.DataFrame()

    items = sorted(filtered.items())
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in items], name="date")
    closes = [c for _, c in items]
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0] * len(closes),
        },
        index=idx,
    )
    return df


def fetch_kospi_index_series(
    start_yyyymmdd: str, end_yyyymmdd: str
) -> pd.DataFrame:
    """외부 소스에서 KOSPI 종합지수 시계열을 새로 가져온다.

    Args:
        start_yyyymmdd: 시작일 "YYYYMMDD"
        end_yyyymmdd: 종료일 "YYYYMMDD"

    Returns:
        DataFrame(index=date, columns=[open, high, low, close, volume]).
        실패 시 빈 DataFrame.
    """
    sd = datetime.strptime(start_yyyymmdd.replace("-", ""), "%Y%m%d").date()
    ed = datetime.strptime(end_yyyymmdd.replace("-", ""), "%Y%m%d").date()

    df = _fetch_naver_kospi_index_series(sd, ed)
    if not df.empty:
        logger.info(
            "KOSPI 지수 시계열 수집(Naver): %d건 (%s ~ %s)",
            len(df), df.index.min().date(), df.index.max().date(),
        )
        return df

    # KRX Open API 폴백 (단일 날짜씩, 백필에는 비효율적이지만 최후의 수단)
    df = _fetch_krx_api_kospi_index_series(sd, ed)
    if not df.empty:
        logger.info(
            "KOSPI 지수 시계열 수집(KRX API): %d건 (%s ~ %s)",
            len(df), df.index.min().date(), df.index.max().date(),
        )
        return df

    logger.warning(
        "KOSPI 지수 시계열 수집 실패 (%s ~ %s)",
        start_yyyymmdd, end_yyyymmdd,
    )
    return pd.DataFrame()


def _fetch_krx_api_kospi_index_series(
    start_date: date, end_date: date
) -> pd.DataFrame:
    """KRX Open API로 KOSPI 인덱스 시계열을 수집 (영업일별 1회 호출).

    백필에는 부담스럽지만 Naver 실패 시 최후의 수단.
    """
    try:
        from pykrx_openapi import KRXOpenAPI
    except ImportError:
        return pd.DataFrame()

    api = KRXOpenAPI()
    rows: list[tuple[date, float, float, float, float]] = []
    cur = start_date
    while cur <= end_date:
        # 주말 스킵 (휴일은 KRX 응답이 비어 있어 자연스럽게 걸러짐)
        if cur.weekday() < 5:
            try:
                resp = api.get_kospi_daily_trade(cur.strftime("%Y%m%d"))
                block = resp.get("OutBlock_1", []) if isinstance(resp, dict) else []
                kospi_row = next(
                    (r for r in block if (r.get("IDX_NM") or "").strip() == "코스피"),
                    None,
                )
                if kospi_row:
                    close = float(kospi_row.get("CLSPRC_IDX") or 0)
                    open_ = float(kospi_row.get("OPNPRC_IDX") or close)
                    high = float(kospi_row.get("HGPRC_IDX") or close)
                    low = float(kospi_row.get("LWPRC_IDX") or close)
                    if close > 0:
                        rows.append((cur, open_, high, low, close))
            except Exception as e:
                logger.debug("KRX API KOSPI 인덱스 %s 실패: %s", cur, e)
        cur += timedelta(days=1)

    if not rows:
        return pd.DataFrame()

    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, *_ in rows], name="date")
    df = pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [0] * len(rows),
        },
        index=idx,
    )
    return df


def get_or_load_kospi_index(
    storage,
    start_yyyymmdd: str,
    end_yyyymmdd: str,
    fresh_window_days: int = 4,
) -> pd.DataFrame:
    """KOSPI 종합지수 시계열을 캐시 우선으로 로드한다.

    캐시 충분/신선 → 그대로 반환. 부족/stale → 외부 소스 보충 후 저장.

    Args:
        storage: DataStorage 인스턴스
        start_yyyymmdd: 시작일 "YYYYMMDD"
        end_yyyymmdd: 종료일 "YYYYMMDD"
        fresh_window_days: 캐시 끝 날짜와 요청 종료일의 허용 차이 (기본 4일)

    Returns:
        DataFrame(index=date, columns=[open, high, low, close, volume])
    """
    sd = datetime.strptime(start_yyyymmdd.replace("-", ""), "%Y%m%d").date()
    ed = datetime.strptime(end_yyyymmdd.replace("-", ""), "%Y%m%d").date()

    cached = storage.load_daily_prices(KOSPI_INDEX_TICKER, sd, ed)
    request_days = (ed - sd).days
    cache_fresh = False
    cache_long_enough = False
    if not cached.empty:
        cache_max = pd.Timestamp(cached.index.max()).date()
        cache_fresh = (ed - cache_max).days <= fresh_window_days
        cache_long_enough = len(cached) >= max(int(request_days * 0.5), 3)

    if cache_fresh and cache_long_enough:
        return cached

    # 외부에서 가져와 캐시 갱신
    fetched = fetch_kospi_index_series(start_yyyymmdd, end_yyyymmdd)
    if fetched.empty:
        # 외부 실패 시 기존 캐시라도 반환 (부분적이지만 폴백)
        if not cached.empty:
            logger.warning(
                "KOSPI 지수 외부 조회 실패 — 부분 캐시 %d건 반환", len(cached)
            )
            return cached
        return pd.DataFrame()

    saved = storage.save_daily_prices(
        KOSPI_INDEX_TICKER, fetched, market=KOSPI_INDEX_MARKET
    )
    logger.info("KOSPI 지수 캐시 저장: %d건", saved)

    # 저장 후 다시 로드 (병합된 결과)
    merged = storage.load_daily_prices(KOSPI_INDEX_TICKER, sd, ed)
    return merged if not merged.empty else fetched


def update_kospi_index_for_date(storage, date_yyyymmdd: str) -> bool:
    """단일 날짜에 대한 KOSPI 지수 캐시를 갱신한다 (스케줄러 일별 수집용).

    Args:
        storage: DataStorage 인스턴스
        date_yyyymmdd: 갱신 대상일 "YYYYMMDD"

    Returns:
        성공 여부.
    """
    target = datetime.strptime(date_yyyymmdd.replace("-", ""), "%Y%m%d").date()
    # 단일 날짜 갱신: 최근 페이지(1)만 가져와 캐시 누락분을 채운다.
    rows = _fetch_naver_page(1)
    if rows:
        for d, close in rows:
            if d == target:
                df = pd.DataFrame(
                    {
                        "open": [close],
                        "high": [close],
                        "low": [close],
                        "close": [close],
                        "volume": [0],
                    },
                    index=pd.DatetimeIndex([pd.Timestamp(d)], name="date"),
                )
                storage.save_daily_prices(
                    KOSPI_INDEX_TICKER, df, market=KOSPI_INDEX_MARKET
                )
                logger.info(
                    "[%s] KOSPI 지수 갱신: close=%.2f (Naver)",
                    date_yyyymmdd, close,
                )
                return True
        logger.debug(
            "[%s] Naver 1페이지에 해당 일자 없음 — 외부 소스 폴백",
            date_yyyymmdd,
        )

    # 폴백: KRX Open API
    df = _fetch_krx_api_kospi_index_series(target, target)
    if not df.empty:
        storage.save_daily_prices(
            KOSPI_INDEX_TICKER, df, market=KOSPI_INDEX_MARKET
        )
        logger.info(
            "[%s] KOSPI 지수 갱신: close=%.2f (KRX API)",
            date_yyyymmdd, float(df["close"].iloc[-1]),
        )
        return True

    logger.warning("[%s] KOSPI 지수 갱신 실패", date_yyyymmdd)
    return False
