# data/dart_client.py
"""DART OpenAPI 클라이언트 - 재무제표 기반 펀더멘털 데이터 수집

DART(전자공시시스템)에서 EPS, 자본총계를 가져와
KRX 시세 데이터와 결합하여 PER/PBR/BPS를 계산합니다.

사용 API:
  - corpCode.xml: 기업 고유번호 목록 (ticker <-> corp_code 매핑)
  - fnlttMultiAcnt.json: 다중회사 주요계정 (배치 조회, 최대 100개)
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


class DartClient:
    """DART OpenAPI 클라이언트

    DART에서 재무제표 데이터를 수집하여
    EPS, BPS, PER, PBR 등 펀더멘털 지표를 계산합니다.
    """

    CORP_CODE_CACHE_PATH = "data/dart_corp_codes.json"

    def __init__(
        self,
        api_key: Optional[str] = None,
        request_delay: float = 0.5,
    ) -> None:
        self.api_key = api_key or settings.dart_api_key
        self.delay = request_delay
        self._corp_code_map: Optional[dict[str, str]] = None

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
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
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
            # 연간 보고서로 재시도
            prev_year = str(int(bsns_year) - 1)
            logger.info(f"[{date_str}] {bsns_year}년 데이터 없음, {prev_year}년 연간 재시도")
            raw_data = self._fetch_multi_account_batch(
                valid_tickers, prev_year, REPRT_CODES["annual"]
            )

        if not raw_data:
            logger.warning(f"[{date_str}] DART 재무제표 데이터 없음")
            return pd.DataFrame()

        eps_map, net_income_map, equity_map = self._extract_financial_items(
            raw_data
        )

        # PER/PBR/BPS/EPS 계산
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

            rows.append({
                "ticker": ticker,
                "EPS": eps,
                "BPS": bps,
                "PER": per,
                "PBR": pbr,
                "DIV": None,
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).set_index("ticker")
        n_eps = df["EPS"].notna().sum()
        n_pbr = df["PBR"].notna().sum()
        logger.info(
            f"[{date_str}] DART 펀더멘털 완료: {len(df)}개 종목 "
            f"(EPS={n_eps}, PBR={n_pbr})"
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
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        """DART 응답에서 EPS, 당기순이익, 자본총계 추출

        fnlttMultiAcnt는 주요 재무제표 계정만 반환하므로
        "기본주당이익" 대신 "당기순이익"을 가져와 EPS를 직접 계산합니다.
        연결재무제표(CFS) 우선, 없으면 별도재무제표(OFS) 사용.

        Returns:
            (eps_map, net_income_map, equity_map) - {ticker: value}
        """
        eps_data: dict[str, dict[str, float]] = {}
        net_income_data: dict[str, dict[str, float]] = {}
        equity_data: dict[str, dict[str, float]] = {}

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

        # 연결(CFS) 우선
        eps_map: dict[str, float] = {}
        for ticker, vals in eps_data.items():
            eps_map[ticker] = vals.get("CFS", vals.get("OFS", 0))

        net_income_map: dict[str, float] = {}
        for ticker, vals in net_income_data.items():
            net_income_map[ticker] = vals.get("CFS", vals.get("OFS", 0))

        equity_map: dict[str, float] = {}
        for ticker, vals in equity_data.items():
            equity_map[ticker] = vals.get("CFS", vals.get("OFS", 0))

        logger.info(
            f"DART 추출: EPS(직접) {len(eps_map)}개, "
            f"당기순이익 {len(net_income_map)}개, "
            f"자본총계 {len(equity_map)}개 종목"
        )
        return eps_map, net_income_map, equity_map

    @staticmethod
    def _parse_amount(s: str) -> Optional[float]:
        """DART 금액 문자열 파싱 (콤마 제거)"""
        if not s or s == "-":
            return None
        try:
            return float(s.replace(",", ""))
        except (ValueError, TypeError):
            return None
