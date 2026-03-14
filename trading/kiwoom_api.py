# trading/kiwoom_api.py
import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # 초


def _safe_int(val: object, default: int = 0) -> int:
    """API 응답 값을 안전하게 int 변환 (쉼표, 대시, 빈값 처리)"""
    if val is None:
        return default
    s = str(val).replace(",", "").replace("-", "").strip()
    if not s:
        return default
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def _safe_float(val: object, default: float = 0.0) -> float:
    """API 응답 값을 안전하게 float 변환 (쉼표, 대시, 빈값 처리)"""
    if val is None:
        return default
    s = str(val).replace(",", "").strip()
    if not s or s == "-":
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


class KiwoomRestClient:
    """키움 REST API 클라이언트

    공식 확인 도메인:
      운영: https://api.kiwoom.com
      모의: https://mockapi.kiwoom.com (KRX 거래만 지원)

    토큰 발급 (au10001):
      POST /oauth2/token
      요청: { grant_type, appkey, secretkey }
      응답: { token, expires_dt, token_type, return_code, return_msg }
    """

    REAL_URL = "https://api.kiwoom.com"
    MOCK_URL = "https://mockapi.kiwoom.com"

    def __init__(self) -> None:
        self.is_paper: bool = settings.is_paper_trading
        self.base_url: str = self.MOCK_URL if self.is_paper else self.REAL_URL
        self.app_key: str = settings.kiwoom_app_key
        self.app_secret: str = settings.kiwoom_app_secret
        self.account_no: str = settings.kiwoom_account_no

        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._last_request_at: float = 0.0
        self._min_request_interval: float = 0.2  # 최소 요청 간격 (초)

        mode = (
            "모의투자 (mockapi.kiwoom.com)"
            if self.is_paper
            else "실전투자 (api.kiwoom.com)"
        )
        logger.info(f"KiwoomRestClient 초기화 [{mode}]")

    def _throttle(self) -> None:
        """API 요청 간 최소 간격 유지 (Rate Limiting)"""
        now = time.time()
        elapsed = now - self._last_request_at
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_at = time.time()

    # ────────────────────────────────────────────
    # 인증
    # ────────────────────────────────────────────

    @property
    def token(self) -> str:
        """토큰 자동 갱신 프로퍼티 (만료 10분 전 재발급)"""
        now = datetime.now()
        if (
            self._token is None
            or self._token_expires_at is None
            or now >= self._token_expires_at - timedelta(minutes=10)
        ):
            self._issue_token()
        if self._token is None:
            raise RuntimeError("토큰 발급에 실패했습니다")
        return self._token

    def _issue_token(self) -> None:
        """액세스 토큰 발급 (au10001)

        POST /oauth2/token
        응답 필드: token (access_token 아님), expires_dt (YYYYMMDDHHmmss)
        """
        url = f"{self.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        try:
            self._throttle()
            resp = requests.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("return_code") != 0:
                raise RuntimeError(f"토큰 발급 실패: {data.get('return_msg')}")

            self._token = data["token"]

            expires_str = data.get("expires_dt", "")
            if expires_str:
                self._token_expires_at = datetime.strptime(expires_str, "%Y%m%d%H%M%S")
            else:
                self._token_expires_at = datetime.now() + timedelta(hours=23)

            logger.info(f"토큰 발급 성공 (만료: {self._token_expires_at})")

        except Exception as e:
            logger.error(f"토큰 발급 오류: {e}")
            raise

    def _headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict:
        """공통 요청 헤더

        Args:
            api_id: TR 코드 (예: ka10001, kt10000)
            cont_yn: 연속 조회 여부
            next_key: 연속 조회 키

        Returns:
            헤더 dict
        """
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self.token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }

    # ────────────────────────────────────────────
    # 시세 조회
    # ────────────────────────────────────────────

    def get_current_price(self, ticker: str) -> dict:
        """주식 현재가 조회 (ka10001)

        GET /api/dostk/mrkt-info

        Args:
            ticker: 종목코드 (예: 005930)

        Returns:
            가격 정보 dict 또는 빈 dict (실패 시)
        """
        url = f"{self.base_url}/api/dostk/mrkt-info"
        try:
            data = self._get_with_retry(url, "ka10001", {"stk_cd": ticker})
            return {
                "ticker": ticker,
                "current_price": _safe_int(data.get("cur_prc")),
                "open_price": _safe_int(data.get("opng_prc")),
                "high_price": _safe_int(data.get("hgst_prc")),
                "low_price": _safe_int(data.get("lwst_prc")),
                "volume": _safe_int(data.get("acc_trd_qty")),
                "change_rate": _safe_float(data.get("flu_rt")),
            }
        except Exception as e:
            logger.error(f"현재가 조회 실패 ({ticker}): {e}")
            return {}

    def _request_with_retry(
        self, method: str, url: str, api_id: str, **kwargs: object
    ) -> dict:
        """HTTP 요청 + 지수 백오프 재시도

        Args:
            method: HTTP 메서드 ('GET' 또는 'POST')
            url: 요청 URL
            api_id: TR 코드
            **kwargs: requests.request()에 전달할 추가 인자 (params, json 등)

        Returns:
            응답 JSON dict
        """
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                self._throttle()
                resp = requests.request(
                    method,
                    url,
                    headers=self._headers(api_id),
                    timeout=10,
                    **kwargs,
                )
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ConnectionError) as e:
                last_exc = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        f"{method} 재시도 ({attempt + 1}/{MAX_RETRIES}): {e}"
                    )
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _get_with_retry(self, url: str, api_id: str, params: dict) -> dict:
        """GET 요청 + 재시도

        Args:
            url: 요청 URL
            api_id: TR 코드
            params: 쿼리 파라미터

        Returns:
            응답 JSON dict
        """
        return self._request_with_retry("GET", url, api_id, params=params)

    def _post_with_retry(self, url: str, api_id: str, body: dict) -> dict:
        """POST 요청 + 재시도

        Args:
            url: 요청 URL
            api_id: TR 코드
            body: 요청 본문

        Returns:
            응답 JSON dict
        """
        return self._request_with_retry("POST", url, api_id, json=body)

    def _post_order(self, url: str, api_id: str, body: dict) -> dict:
        """주문 전용 POST 요청 (재시도 없음 — 중복 주문 방지)

        주문(매수/매도)은 서버가 처리 후 응답 전 네트워크 끊김 시
        재시도하면 동일 주문이 중복 체결될 위험이 있으므로 단 1회만 시도합니다.
        단, HTTP 401(토큰 만료)은 토큰 갱신 후 1회 재시도합니다.

        Args:
            url: 요청 URL
            api_id: TR 코드
            body: 요청 본문

        Returns:
            응답 JSON dict
        """
        self._throttle()
        resp = requests.post(
            url,
            headers=self._headers(api_id),
            json=body,
            timeout=10,
        )
        if resp.status_code == 401:
            logger.warning("주문 중 401 수신 — 토큰 강제 갱신 후 1회 재시도")
            self._token = None
            self._token_expires_at = None
            resp = requests.post(
                url,
                headers=self._headers(api_id),
                json=body,
                timeout=10,
            )
        resp.raise_for_status()
        return resp.json()

    # ────────────────────────────────────────────
    # 주문
    # ────────────────────────────────────────────

    def buy_stock(
        self,
        ticker: str,
        qty: int,
        price: int = 0,
        order_type: str = "3",
        exchange: str = "SOR",
    ) -> dict:
        """국내 주식 매수 주문 (kt10000)

        POST /api/dostk/ordr

        Args:
            ticker: 종목코드
            qty: 매수 수량
            price: 주문 가격 (시장가=0)
            order_type: '0'=지정가, '3'=시장가
            exchange: 'KRX', 'NXT', 'SOR'

        Returns:
            응답 dict 또는 빈 dict (실패 시)
        """
        if not self.is_paper:
            logger.warning(f"[실전] 매수 주문: {ticker} {qty}주")

        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": ticker,
            "ord_qty": str(qty),
            "ord_uv": str(price),
            "trde_tp": order_type,
            "acnt_no": self.account_no,
        }
        try:
            data = self._post_order(url, "kt10000", body)
            if data.get("return_code") == 0:
                logger.info(
                    f"매수 완료: {ticker} {qty}주 (주문번호: {data.get('ord_no')})"
                )
            else:
                logger.error(f"매수 실패: {data.get('return_msg')}")
            return data
        except Exception as e:
            logger.error(f"매수 주문 오류 ({ticker}): {e}")
            return {}

    def sell_stock(
        self,
        ticker: str,
        qty: int,
        price: int = 0,
        order_type: str = "3",
        exchange: str = "SOR",
    ) -> dict:
        """국내 주식 매도 주문 (kt10001)

        POST /api/dostk/ordr (api-id: kt10001)

        Args:
            ticker: 종목코드
            qty: 매도 수량
            price: 주문 가격 (시장가=0)
            order_type: '0'=지정가, '3'=시장가
            exchange: 'KRX', 'NXT', 'SOR'

        Returns:
            응답 dict 또는 빈 dict (실패 시)
        """
        if not self.is_paper:
            logger.warning(f"[실전] 매도 주문: {ticker} {qty}주")

        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": ticker,
            "ord_qty": str(qty),
            "ord_uv": str(price),
            "trde_tp": order_type,
            "acnt_no": self.account_no,
        }
        try:
            data = self._post_order(url, "kt10001", body)
            if data.get("return_code") == 0:
                logger.info(
                    f"매도 완료: {ticker} {qty}주 (주문번호: {data.get('ord_no')})"
                )
            else:
                logger.error(f"매도 실패: {data.get('return_msg')}")
            return data
        except Exception as e:
            logger.error(f"매도 주문 오류 ({ticker}): {e}")
            return {}

    def cancel_order(self, orig_order_no: str, ticker: str, qty: int) -> dict:
        """주문 취소 (kt10002)

        Args:
            orig_order_no: 원주문번호
            ticker: 종목코드
            qty: 취소 수량

        Returns:
            응답 dict 또는 빈 dict (실패 시)
        """
        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "orgn_ord_no": orig_order_no,
            "stk_cd": ticker,
            "ord_qty": str(qty),
            "acnt_no": self.account_no,
        }
        try:
            data = self._post_order(url, "kt10002", body)
            logger.info(f"주문 취소: {orig_order_no} → {data.get('return_msg')}")
            return data
        except Exception as e:
            logger.error(f"주문 취소 오류: {e}")
            return {}

    # ────────────────────────────────────────────
    # 계좌 조회
    # ────────────────────────────────────────────

    def get_balance(self) -> dict:
        """계좌 잔고 조회 (kt00018)

        GET /api/dostk/acnt

        Returns:
            잔고 dict {holdings, cash, total_eval_amount, total_profit}
        """
        url = f"{self.base_url}/api/dostk/acnt"
        try:
            data = self._get_with_retry(url, "kt00018", {"acnt_no": self.account_no})

            holdings = []
            for item in data.get("acnt_evlt_remn_indv_tot", []):
                holdings.append(
                    {
                        "ticker": item.get("stk_cd", ""),
                        "name": item.get("stk_nm", ""),
                        "qty": _safe_int(item.get("rmnd_qty")),
                        "avg_price": _safe_float(item.get("avg_prc")),
                        "current_price": _safe_float(item.get("cur_prc")),
                        "eval_amount": _safe_float(item.get("evlt_amt")),
                        "eval_profit": _safe_float(item.get("evlt_pfls")),
                        "profit_rate": _safe_float(item.get("pfls_rt")),
                    }
                )

            return {
                "holdings": holdings,
                "cash": _safe_float(data.get("dnca_tot_amt")),
                "total_eval_amount": _safe_float(data.get("tot_evlt_amt")),
                "total_profit": _safe_float(data.get("tot_pfls")),
            }
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return {
                "holdings": [],
                "cash": 0,
                "total_eval_amount": 0,
                "total_profit": 0,
            }

    def get_unfilled_orders(self) -> list:
        """미체결 주문 조회 (kt00013)

        Returns:
            미체결 주문 리스트
        """
        url = f"{self.base_url}/api/dostk/acnt"
        try:
            data = self._get_with_retry(url, "kt00013", {"acnt_no": self.account_no})
            return data.get("oso_ord_list", [])
        except Exception as e:
            logger.error(f"미체결 조회 실패: {e}")
            return []

    def ping(self) -> bool:
        """API 연결 확인 (토큰 발급 성공 여부)

        Returns:
            True=연결 성공, False=실패
        """
        try:
            _ = self.token
            return True
        except Exception:
            return False
