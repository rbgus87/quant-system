# data/dart_client.py
"""DART OpenAPI 클라이언트 - 재무제표 기반 펀더멘털 데이터 수집

DART(전자공시시스템)에서 EPS, 자본총계, DPS를 가져와
KRX 시세 데이터와 결합하여 PER/PBR/DIV을 계산합니다.

사용 API:
  - corpCode.xml: 기업 고유번호 목록 (ticker <-> corp_code 매핑)
  - fnlttMultiAcnt.json: 다중회사 주요계정 (배치 조회, 최대 100개)
  - alotMatter.json: 배당에 관한 사항 (개별 조회, DPS 추출)
"""

import io
import json
import logging
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from tqdm import tqdm

from config.settings import settings

logger = logging.getLogger(__name__)

DART_BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드
REPRT_CODES = {
    "annual": "11011",  # 사업보고서
    "q3": "11014",  # 3분기보고서
    "half": "11012",  # 반기보고서
    "q1": "11013",  # 1분기보고서
}

# DART 재무제표에서 추출할 계정명
EPS_ACCOUNT_NAMES = {"기본주당이익", "기본주당이익(손실)", "기본주당순이익", "주당이익"}
NET_INCOME_ACCOUNT_NAMES = {"당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"}
EQUITY_ACCOUNT_NAMES = {"자본총계"}
OPERATING_CF_ACCOUNT_NAMES = {"영업활동현금흐름", "영업활동으로인한현금흐름"}
REVENUE_ACCOUNT_NAMES = {"매출액", "수익(매출액)", "영업수익"}
OPERATING_INCOME_ACCOUNT_NAMES = {"영업이익", "영업이익(손실)"}
TOTAL_ASSETS_ACCOUNT_NAMES = {"자산총계"}


class DartClient:
    """DART OpenAPI 클라이언트

    DART에서 재무제표 데이터를 수집하여
    EPS, BPS, PER, PBR 등 펀더멘털 지표를 계산합니다.
    """

    CORP_CODE_CACHE_PATH = "data/dart_corp_codes.json"
    DPS_CACHE_PATH = "data/dart_dps_cache.json"

    def __init__(
        self,
        api_key: Optional[str] = None,
        request_delay: float = 0.5,
    ) -> None:
        self.api_key = api_key or settings.dart_api_key
        self.delay = request_delay
        self._corp_code_map: Optional[dict[str, str]] = None
        self._dps_cache: Optional[dict[str, dict[str, float]]] = None

        if not self.api_key:
            logger.warning("DART_API_KEY 미설정. .env 파일에 추가하세요.")

    @property
    def corp_code_map(self) -> dict[str, str]:
        """ticker -> corp_code 매핑 (lazy load + 파일 캐시)"""
        if self._corp_code_map is None:
            self._corp_code_map = self._load_corp_codes()
        return self._corp_code_map

    # ───────────────────────────────────────────────
    # Corp Code 매핑
    # ───────────────────────────────────────────────

    def _load_corp_codes(self) -> dict[str, str]:
        """기업 고유번호 목록 로드 (파일 캐시 -> API 다운로드)

        Returns:
            {ticker: corp_code} 예: {"005930": "00126380"}
        """
        cache_path = Path(self.CORP_CODE_CACHE_PATH)

        # 캐시 확인 (7일 이내면 재사용)
        if cache_path.exists():
            try:
                age_days = (time.time() - cache_path.stat().st_mtime) / 86400
                if age_days < 7:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    logger.info(f"DART corp code 캐시 로드: {len(data)}개 기업")
                    return data
            except Exception as e:
                logger.warning(f"Corp code 캐시 로드 실패: {e}")

        # API 다운로드
        if not self.api_key:
            logger.error("DART API 키 없음 - corp code 다운로드 불가")
            return {}

        try:
            resp = requests.get(
                f"{DART_BASE_URL}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=30,
            )
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_data = zf.read(zf.namelist()[0])

            root = ET.fromstring(xml_data)
            mapping: dict[str, str] = {}

            for item in root.iter("list"):
                corp_code = (item.findtext("corp_code") or "").strip()
                stock_code = (item.findtext("stock_code") or "").strip()
                if stock_code and corp_code:
                    mapping[stock_code.zfill(6)] = corp_code

            # 캐시 저장
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False)

            logger.info(f"DART corp code 다운로드 완료: {len(mapping)}개 상장 기업")
            return mapping

        except Exception as e:
            logger.error(f"DART corp code 다운로드 실패: {e}")
            return {}

    # ───────────────────────────────────────────────
    # DPS (주당배당금) 캐시
    # ───────────────────────────────────────────────

    @property
    def dps_cache(self) -> dict[str, dict[str, float]]:
        """DPS 캐시 (lazy load): {bsns_year: {ticker: dps}}"""
        if self._dps_cache is None:
            self._dps_cache = self._load_dps_cache()
        return self._dps_cache

    def _load_dps_cache(self) -> dict[str, dict[str, float]]:
        """DPS 캐시 파일 로드"""
        cache_path = Path(self.DPS_CACHE_PATH)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"DPS 캐시 로드 실패: {e}")
        return {}

    def _save_dps_cache(self) -> None:
        """DPS 캐시 파일 저장"""
        try:
            cache_path = Path(self.DPS_CACHE_PATH)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(self._dps_cache, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"DPS 캐시 저장 실패: {e}")

    def get_dps_for_tickers(
        self,
        tickers: list[str],
        bsns_year: str,
        reprt_code: str,
    ) -> dict[str, float]:
        """종목 리스트의 DPS(주당 현금배당금) 조회 (캐시 우선)

        DART alotMatter API에서 보통주 주당 현금배당금을 추출합니다.
        연도별로 캐시하여 동일 사업연도 재조회 시 API 호출을 생략합니다.

        Args:
            tickers: 종목코드 리스트
            bsns_year: 사업연도
            reprt_code: 보고서 코드 (사업보고서=11011 권장)

        Returns:
            {ticker: dps} 매핑
        """
        if not self.api_key:
            return {}

        cache_key = f"{bsns_year}_{reprt_code}"
        year_cache = self.dps_cache.get(cache_key, {})

        # 캐시 히트 종목과 미스 종목 분리
        result: dict[str, float] = {}
        missing: list[str] = []
        for t in tickers:
            if t in year_cache:
                dps = year_cache[t]
                if dps > 0:
                    result[t] = dps
            elif t in self.corp_code_map:
                missing.append(t)

        if not missing:
            return result

        logger.info(
            f"[{bsns_year}] DPS 조회: {len(missing)}개 종목 "
            f"(캐시 히트: {len(tickers) - len(missing)}개)"
        )

        # alotMatter API 병렬 호출 (ThreadPoolExecutor)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        fetched = 0
        total = len(missing)
        rate_semaphore = threading.Semaphore(2)  # 초당 최대 2개 요청

        def _rate_limited_fetch(ticker: str) -> tuple[str, Optional[float]]:
            """Rate-limited 단일 종목 DPS 조회"""
            corp_code = self.corp_code_map.get(ticker)
            if not corp_code:
                return ticker, None
            rate_semaphore.acquire()
            try:
                dps = self._fetch_dps_single(corp_code, bsns_year, reprt_code)
                return ticker, dps
            finally:
                # 0.5초 후 세마포어 해제 (초당 요청 수 제한)
                threading.Timer(0.5, rate_semaphore.release).start()

        pbar = tqdm(
            total=total,
            desc=f"[{bsns_year}] DPS 조회",
            unit="종목",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(_rate_limited_fetch, ticker): ticker
                for ticker in missing
            }
            for future in as_completed(futures):
                try:
                    ticker, dps = future.result()
                    year_cache[ticker] = dps if dps is not None else 0.0
                    if dps is not None and dps > 0:
                        result[ticker] = dps
                        fetched += 1
                except Exception:
                    logger.warning(
                        f"DPS 조회 실패: {futures[future]}", exc_info=True
                    )

                completed += 1
                pbar.update(1)
                pbar.set_postfix(유효=fetched, refresh=False)

                if completed % 50 == 0:
                    self.dps_cache[cache_key] = year_cache
                    self._save_dps_cache()

        pbar.close()

        # 캐시 저장
        self.dps_cache[cache_key] = year_cache
        self._save_dps_cache()

        logger.info(
            f"[{bsns_year}] DPS 조회 완료: {fetched}/{len(missing)}개 종목 유효"
        )
        return result

    def _fetch_dps_single(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str,
    ) -> Optional[float]:
        """단일 기업 DPS 조회 (alotMatter API)

        보통주 주당 현금배당금(원) 추출.

        Args:
            corp_code: DART 기업 고유번호
            bsns_year: 사업연도
            reprt_code: 보고서 코드

        Returns:
            주당 현금배당금 (원) 또는 None
        """
        try:
            resp = requests.get(
                f"{DART_BASE_URL}/alotMatter.json",
                params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": reprt_code,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "000":
                return None

            for item in data.get("list", []):
                se = item.get("se", "")
                stock_knd = item.get("stock_knd", "")
                if se == "주당 현금배당금(원)" and stock_knd == "보통주":
                    return self._parse_amount(item.get("thstrm", ""))

            return None

        except Exception as e:
            logger.debug(f"DPS 조회 실패 (corp_code={corp_code}): {e}")
            return None

    # ───────────────────────────────────────────────
    # 펀더멘털 데이터 조회
    # ───────────────────────────────────────────────

    def get_fundamentals_for_date(
        self,
        tickers: list[str],
        date_str: str,
        close_prices: pd.Series,
        shares: pd.Series,
    ) -> pd.DataFrame:
        """특정 날짜 기준 펀더멘털 데이터 조회 + PER/PBR 계산

        Args:
            tickers: 종목코드 리스트
            date_str: 기준 날짜 (YYYYMMDD)
            close_prices: 종가 Series (index=ticker)
            shares: 발행주식수 Series (index=ticker)

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, PCR, EPS, DIV])
        """
        if not self.api_key:
            return pd.DataFrame()

        bsns_year, reprt_code = self._determine_report_period(date_str)

        valid_tickers = [t for t in tickers if t in self.corp_code_map]
        if not valid_tickers:
            logger.warning("DART corp code에 매핑되는 종목 없음")
            return pd.DataFrame()

        reprt_label = {
            "11011": "연간",
            "11013": "1분기",
            "11012": "반기",
            "11014": "3분기",
        }.get(reprt_code, reprt_code)
        logger.info(
            f"[{date_str}] DART 재무제표 조회: {len(valid_tickers)}개 종목, "
            f"사업연도={bsns_year}, 보고서={reprt_label}"
        )

        # 배치 조회
        raw_data = self._fetch_multi_account_batch(
            valid_tickers, bsns_year, reprt_code
        )

        if not raw_data:
            # 연간 보고서로 최대 3년까지 하향 탐색 (오래된 데이터 폴백)
            base_year = int(bsns_year)
            for offset in range(1, 4):
                try_year = str(base_year - offset)
                logger.info(f"[{date_str}] {bsns_year}년 데이터 없음, {try_year}년 연간 재시도")
                raw_data = self._fetch_multi_account_batch(
                    valid_tickers, try_year, REPRT_CODES["annual"]
                )
                if raw_data:
                    break

        if not raw_data:
            logger.warning(f"[{date_str}] DART 재무제표 데이터 없음")
            return pd.DataFrame()

        (
            eps_map, net_income_map, equity_map, operating_cf_map,
            revenue_map, operating_income_map, total_assets_map,
        ) = self._extract_financial_items(raw_data)

        # DPS (주당배당금) 조회 → DIV 계산용
        # 배당 데이터는 사업보고서(연간)에서만 제공
        dps_year, dps_reprt = self._determine_dps_report_period(date_str)
        dps_map = self.get_dps_for_tickers(valid_tickers, dps_year, dps_reprt)

        # PER/PBR/BPS/EPS/DIV 계산
        rows = []
        for ticker in valid_tickers:
            total_equity = equity_map.get(ticker)
            close = close_prices.get(ticker)
            num_shares = shares.get(ticker)

            if total_equity is None and ticker not in eps_map and ticker not in net_income_map:
                continue

            # EPS: DART 직접 제공 우선, 없으면 당기순이익/주식수로 계산
            eps = eps_map.get(ticker)
            if eps is None and num_shares and num_shares > 0:
                net_income = net_income_map.get(ticker)
                if net_income is not None:
                    eps = net_income / num_shares

            # BPS = 자본총계 / 발행주식수
            bps = None
            if total_equity is not None and num_shares and num_shares > 0:
                bps = total_equity / num_shares

            # PER = 종가 / EPS (흑자 기업만)
            per = None
            if close and eps and eps > 0:
                per = close / eps

            # PBR = 종가 / BPS
            pbr = None
            if close and bps and bps > 0:
                pbr = close / bps

            # PCR = 종가 / (영업활동현금흐름 / 발행주식수)
            # fnlttMultiAcnt는 CF 항목을 반환하지 않으므로 대부분 None
            pcr = None
            op_cf = operating_cf_map.get(ticker)
            if close and op_cf and num_shares and num_shares > 0:
                cfps = op_cf / num_shares
                if cfps > 0:
                    pcr = close / cfps

            # PSR = 시가총액 / 매출액 (PCR 폴백)
            psr = None
            revenue = revenue_map.get(ticker)
            if close and revenue and revenue > 0 and num_shares and num_shares > 0:
                market_cap_val = close * num_shares
                psr = market_cap_val / revenue

            # 영업이익, 총자산 (GP/A 폴백용 OP/A 계산)
            op_income = operating_income_map.get(ticker)
            total_assets = total_assets_map.get(ticker)

            # DIV = DPS / 종가 × 100 (배당수익률 %)
            div = None
            dps = dps_map.get(ticker)
            if dps and close and close > 0:
                div = dps / close * 100

            rows.append({
                "ticker": ticker,
                "EPS": eps,
                "BPS": bps,
                "PER": per,
                "PBR": pbr,
                "PCR": pcr,
                "PSR": psr,
                "DIV": div,
                "REVENUE": revenue,
                "OPERATING_INCOME": op_income,
                "TOTAL_ASSETS": total_assets,
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("ticker")
        n_eps = df["EPS"].notna().sum()
        n_pbr = df["PBR"].notna().sum()
        n_pcr = df["PCR"].notna().sum() if "PCR" in df.columns else 0
        n_psr = df["PSR"].notna().sum() if "PSR" in df.columns else 0
        n_div = df["DIV"].notna().sum()
        n_ta = df["TOTAL_ASSETS"].notna().sum() if "TOTAL_ASSETS" in df.columns else 0
        logger.info(
            f"[{date_str}] DART 펀더멘털 완료: {len(df)}개 종목 "
            f"(EPS={n_eps}, PBR={n_pbr}, PCR={n_pcr}, PSR={n_psr}, "
            f"DIV={n_div}, 총자산={n_ta})"
        )
        return df

    # ───────────────────────────────────────────────
    # 보고서 기간 결정
    # ───────────────────────────────────────────────

    @staticmethod
    def _determine_report_period(date_str: str) -> tuple[str, str]:
        """기준일 -> DART 보고서 (사업연도, 보고서코드) 결정

        look-ahead bias 방지를 위해 공시 시점 기준:
          - 연간(11011): 3월 말 공시 -> 4월부터 사용
          - 1분기(11013): 5월 중순 공시 -> 6월부터 사용
          - 반기(11012): 8월 중순 공시 -> 9월부터 사용
          - 3분기(11014): 11월 중순 공시 -> 12월부터 사용
        """
        dt = datetime.strptime(date_str.replace("-", ""), "%Y%m%d")
        year = dt.year
        month = dt.month

        if month >= 12:
            return str(year), REPRT_CODES["q3"]
        elif month >= 9:
            return str(year), REPRT_CODES["half"]
        elif month >= 6:
            return str(year), REPRT_CODES["q1"]
        elif month >= 4:
            return str(year - 1), REPRT_CODES["annual"]
        else:
            return str(year - 2), REPRT_CODES["annual"]

    @staticmethod
    def _determine_dps_report_period(date_str: str) -> tuple[str, str]:
        """기준일 -> DPS 조회용 보고서 기간 결정

        배당 데이터(alotMatter)는 사업보고서(연간)에서만 제공됩니다.
        사업보고서는 3월 말 공시 → 4월부터 사용 가능.

        Returns:
            (사업연도, 보고서코드) - 항상 사업보고서(11011)
        """
        dt = datetime.strptime(date_str.replace("-", ""), "%Y%m%d")
        year = dt.year
        month = dt.month

        # 4월 이후: 전년도 사업보고서 사용 가능
        if month >= 4:
            return str(year - 1), REPRT_CODES["annual"]
        # 1~3월: 전전년도 사업보고서 사용
        return str(year - 2), REPRT_CODES["annual"]

    # ───────────────────────────────────────────────
    # API 호출
    # ───────────────────────────────────────────────

    def _fetch_multi_account_batch(
        self,
        tickers: list[str],
        bsns_year: str,
        reprt_code: str,
    ) -> list[dict]:
        """다중회사 주요계정 배치 조회 (100개씩)

        Args:
            tickers: 종목코드 리스트
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 보고서 코드

        Returns:
            DART API 응답의 list 항목 합산
        """
        all_items: list[dict] = []
        batch_size = 100

        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            corp_codes = [
                self.corp_code_map[t] for t in batch if t in self.corp_code_map
            ]
            if not corp_codes:
                continue

            try:
                resp = requests.get(
                    f"{DART_BASE_URL}/fnlttMultiAcnt.json",
                    params={
                        "crtfc_key": self.api_key,
                        "corp_code": ",".join(corp_codes),
                        "bsns_year": bsns_year,
                        "reprt_code": reprt_code,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "")
                if status == "000":
                    items = data.get("list", [])
                    all_items.extend(items)
                    logger.debug(
                        f"DART 배치 {i // batch_size + 1}: "
                        f"{len(corp_codes)}개 요청 -> {len(items)}개 항목"
                    )
                elif status == "013":
                    logger.debug(
                        f"DART 배치 {i // batch_size + 1}: 조회 결과 없음"
                    )
                else:
                    msg = data.get("message", "")
                    logger.warning(
                        f"DART API 오류: status={status}, message={msg}"
                    )

                time.sleep(self.delay)

            except Exception as e:
                logger.warning(f"DART API 배치 조회 실패: {e}")
                time.sleep(self.delay)

        return all_items

    # ───────────────────────────────────────────────
    # 응답 파싱
    # ───────────────────────────────────────────────

    def _extract_financial_items(
        self, items: list[dict]
    ) -> tuple[
        dict[str, float], dict[str, float], dict[str, float],
        dict[str, float], dict[str, float], dict[str, float],
        dict[str, float],
    ]:
        """DART 응답에서 주요 재무 항목 추출

        fnlttMultiAcnt가 반환하는 항목: BS(자산/부채/자본), IS(매출/영업이익/순이익)
        ※ CF(현금흐름표)는 반환하지 않음 → operating_cf_map은 거의 비어 있음
        연결재무제표(CFS) 우선, 없으면 별도재무제표(OFS) 사용.

        Returns:
            (eps_map, net_income_map, equity_map, operating_cf_map,
             revenue_map, operating_income_map, total_assets_map)
        """
        eps_data: dict[str, dict[str, float]] = {}
        net_income_data: dict[str, dict[str, float]] = {}
        equity_data: dict[str, dict[str, float]] = {}
        operating_cf_data: dict[str, dict[str, float]] = {}
        revenue_data: dict[str, dict[str, float]] = {}
        operating_income_data: dict[str, dict[str, float]] = {}
        total_assets_data: dict[str, dict[str, float]] = {}

        for item in items:
            stock_code = (item.get("stock_code") or "").strip()
            if not stock_code:
                continue
            ticker = stock_code.zfill(6)

            account_nm = (item.get("account_nm") or "").strip()
            fs_div = (item.get("fs_div") or "").strip()
            amount_str = (item.get("thstrm_amount") or "").strip()

            amount = self._parse_amount(amount_str)
            if amount is None:
                continue

            if account_nm in EPS_ACCOUNT_NAMES:
                eps_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in NET_INCOME_ACCOUNT_NAMES:
                net_income_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in EQUITY_ACCOUNT_NAMES:
                equity_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in OPERATING_CF_ACCOUNT_NAMES:
                operating_cf_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in REVENUE_ACCOUNT_NAMES:
                revenue_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in OPERATING_INCOME_ACCOUNT_NAMES:
                operating_income_data.setdefault(ticker, {})[fs_div] = amount
            if account_nm in TOTAL_ASSETS_ACCOUNT_NAMES:
                total_assets_data.setdefault(ticker, {})[fs_div] = amount

        def _pick_cfs(data: dict[str, dict[str, float]]) -> dict[str, float]:
            return {t: v.get("CFS", v.get("OFS", 0)) for t, v in data.items()}

        eps_map = _pick_cfs(eps_data)
        net_income_map = _pick_cfs(net_income_data)
        equity_map = _pick_cfs(equity_data)
        operating_cf_map = _pick_cfs(operating_cf_data)
        revenue_map = _pick_cfs(revenue_data)
        operating_income_map = _pick_cfs(operating_income_data)
        total_assets_map = _pick_cfs(total_assets_data)

        logger.info(
            f"DART 추출: EPS {len(eps_map)}, 순이익 {len(net_income_map)}, "
            f"자본 {len(equity_map)}, CF {len(operating_cf_map)}, "
            f"매출 {len(revenue_map)}, 영업이익 {len(operating_income_map)}, "
            f"총자산 {len(total_assets_map)}개 종목"
        )
        return (
            eps_map, net_income_map, equity_map, operating_cf_map,
            revenue_map, operating_income_map, total_assets_map,
        )

    @staticmethod
    def _parse_amount(s: str) -> Optional[float]:
        """DART 금액 문자열 파싱 (콤마 제거)"""
        if not s or s == "-":
            return None
        try:
            return float(s.replace(",", ""))
        except (ValueError, TypeError):
            return None
