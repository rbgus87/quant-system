# 06. 키움 REST API 연동

## 6-1. 공식 확인 사항 (openapi.kiwoom.com 직접 확인)

| 항목 | 값 |
|------|-----|
| API 포털 (가이드/문서) | `https://openapi.kiwoom.com` |
| **운영(실전) 도메인** | `https://api.kiwoom.com` |
| **모의투자 도메인** | `https://mockapi.kiwoom.com` (KRX 거래만 지원) |
| 토큰 발급 URL | `POST /oauth2/token` |
| 토큰 요청 파라미터 | `grant_type`, `appkey`, `secretkey` |
| 토큰 응답 필드 | `token` (**access_token 아님!**) |
| 토큰 만료일 필드 | `expires_dt` (형식: `YYYYMMDDHHmmss`) |
| 성공 코드 | `return_code: 0` |
| 인증 헤더 | `Authorization: Bearer {token}` |
| TR 코드 헤더 | `api-id: {TR코드}` |
| 명세서 다운로드 | openapi.kiwoom.com → API 가이드 → Excel/PDF 버튼 |

> ⚠️ **응답 필드명 (`cur_prc`, `ord_no` 등)은 공식 PDF 명세서 기준으로 반드시 재확인 필요**
> 아래 코드의 응답 파싱 필드명은 참고용 예시이며 실제와 다를 수 있습니다.

---

## 6-2. 신청 절차

```
① 키움증권 계좌 개설 (비대면 가능, kiwoom.com)
② 홈페이지 로그인 → [트레이딩 채널] → [키움 REST API] → 이용 신청
   또는: [고객서비스] → [다운로드] → [Open API] → [키움 REST API]
③ 허용 IP 등록 (포털에서 등록 필수 — 미등록 IP는 요청 차단됨)
④ 모의투자 계정 신청 (실전 전 반드시 먼저 테스트)
⑤ openapi.kiwoom.com → API 가이드 → 명세서 PDF/Excel 다운로드
⑥ .env 파일에 APP_KEY, APP_SECRET 입력
```

---

## 6-3. trading/kiwoom_api.py

```python
# trading/kiwoom_api.py
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class KiwoomRestClient:
    """
    키움 REST API 클라이언트

    공식 확인 도메인:
      운영: https://api.kiwoom.com
      모의: https://mockapi.kiwoom.com  (KRX 거래만 지원)

    토큰 발급 (au10001):
      POST /oauth2/token
      요청: { grant_type, appkey, secretkey }
      응답: { token, expires_dt, token_type, return_code, return_msg }
      ★ 토큰 필드명: "token" (access_token 아님)
      ★ expires_dt 형식: "YYYYMMDDHHmmss"
    """

    REAL_URL = "https://api.kiwoom.com"
    MOCK_URL = "https://mockapi.kiwoom.com"   # KRX만 지원

    def __init__(self):
        self.is_paper = settings.is_paper_trading
        self.base_url = self.MOCK_URL if self.is_paper else self.REAL_URL
        self.app_key  = settings.kiwoom_app_key
        self.app_secret = settings.kiwoom_app_secret
        self.account_no = settings.kiwoom_account_no

        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

        mode = "모의투자 (mockapi.kiwoom.com)" if self.is_paper else "⚠️ 실전투자 (api.kiwoom.com)"
        logger.info(f"KiwoomRestClient 초기화 [{mode}]")

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
        return self._token

    def _issue_token(self):
        """
        액세스 토큰 발급 (au10001)
        POST /oauth2/token
        """
        url = f"{self.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey":     self.app_key,
            "secretkey":  self.app_secret,    # ← secretkey (appsecret 아님)
        }
        try:
            resp = requests.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("return_code") != 0:
                raise RuntimeError(f"토큰 발급 실패: {data.get('return_msg')}")

            self._token = data["token"]        # ← "token" 필드 (access_token 아님)

            # expires_dt 파싱: "20241107083713" → datetime
            expires_str = data.get("expires_dt", "")
            if expires_str:
                self._token_expires_at = datetime.strptime(expires_str, "%Y%m%d%H%M%S")
            else:
                # fallback: 23시간 후 만료 가정
                self._token_expires_at = datetime.now() + timedelta(hours=23)

            logger.info(f"토큰 발급 성공 (만료: {self._token_expires_at})")

        except Exception as e:
            logger.error(f"토큰 발급 오류: {e}")
            raise

    def _headers(self, api_id: str, cont_yn: str = "N", next_key: str = "") -> dict:
        """공통 요청 헤더"""
        return {
            "Content-Type":  "application/json;charset=UTF-8",
            "Authorization": f"Bearer {self.token}",
            "api-id":        api_id,
            "cont-yn":       cont_yn,
            "next-key":      next_key,
        }

    # ────────────────────────────────────────────
    # 시세 조회
    # ────────────────────────────────────────────

    def get_current_price(self, ticker: str) -> dict:
        """
        주식 현재가 조회 (ka10001)
        GET /api/dostk/mrkt-info

        ⚠️ 응답 필드명(cur_prc 등)은 공식 PDF 명세 확인 후 수정 필요
        """
        url = f"{self.base_url}/api/dostk/mrkt-info"
        try:
            resp = requests.get(
                url,
                headers=self._headers("ka10001"),
                params={"stk_cd": ticker},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "ticker":       ticker,
                "current_price": int(data.get("cur_prc", 0)),
                "open_price":    int(data.get("opng_prc", 0)),
                "high_price":    int(data.get("hgst_prc", 0)),
                "low_price":     int(data.get("lwst_prc", 0)),
                "volume":        int(data.get("acc_trd_qty", 0)),
                "change_rate":   float(data.get("flu_rt", 0)),
            }
        except Exception as e:
            logger.error(f"현재가 조회 실패 ({ticker}): {e}")
            return {}

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
        """
        국내 주식 매수 주문 (kt10000)
        POST /api/dostk/ordr

        Args:
            order_type: '0'=지정가, '3'=시장가
            exchange:   'KRX', 'NXT', 'SOR'(자동라우팅, 권장)
                        ⚠️ 모의투자(mockapi)는 KRX만 지원 → 모의 시 'KRX' 사용
        """
        if not self.is_paper:
            logger.warning(f"⚠️ [실전] 매수 주문: {ticker} {qty}주")

        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd":       ticker,
            "ord_qty":      str(qty),
            "ord_uv":       str(price),
            "trde_tp":      order_type,
            "acnt_no":      self.account_no,
        }
        try:
            resp = requests.post(url, headers=self._headers("kt10000"), json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("return_code") == 0:
                logger.info(f"✅ 매수 완료: {ticker} {qty}주 (주문번호: {data.get('ord_no')})")
            else:
                logger.error(f"❌ 매수 실패: {data.get('return_msg')}")
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
        """
        국내 주식 매도 주문 (kt10001)
        POST /api/dostk/ordr  (api-id만 다름: kt10001)
        """
        if not self.is_paper:
            logger.warning(f"⚠️ [실전] 매도 주문: {ticker} {qty}주")

        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd":       ticker,
            "ord_qty":      str(qty),
            "ord_uv":       str(price),
            "trde_tp":      order_type,
            "acnt_no":      self.account_no,
        }
        try:
            resp = requests.post(url, headers=self._headers("kt10001"), json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("return_code") == 0:
                logger.info(f"✅ 매도 완료: {ticker} {qty}주 (주문번호: {data.get('ord_no')})")
            else:
                logger.error(f"❌ 매도 실패: {data.get('return_msg')}")
            return data
        except Exception as e:
            logger.error(f"매도 주문 오류 ({ticker}): {e}")
            return {}

    def cancel_order(self, orig_order_no: str, ticker: str, qty: int) -> dict:
        """주문 취소 (kt10002)"""
        url = f"{self.base_url}/api/dostk/ordr"
        body = {
            "orgn_ord_no": orig_order_no,
            "stk_cd":      ticker,
            "ord_qty":     str(qty),
            "acnt_no":     self.account_no,
        }
        try:
            resp = requests.post(url, headers=self._headers("kt10002"), json=body, timeout=10)
            data = resp.json()
            logger.info(f"주문 취소: {orig_order_no} → {data.get('return_msg')}")
            return data
        except Exception as e:
            logger.error(f"주문 취소 오류: {e}")
            return {}

    # ────────────────────────────────────────────
    # 계좌 조회
    # ────────────────────────────────────────────

    def get_balance(self) -> dict:
        """
        계좌 잔고 조회 (kt00018)
        GET /api/dostk/acnt

        ⚠️ 응답 키명(acnt_evlt_remn_indv_tot 등)은 PDF 명세 확인 필수
        """
        url = f"{self.base_url}/api/dostk/acnt"
        try:
            resp = requests.get(
                url,
                headers=self._headers("kt00018"),
                params={"acnt_no": self.account_no},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            holdings = []
            for item in data.get("acnt_evlt_remn_indv_tot", []):
                holdings.append({
                    "ticker":       item.get("stk_cd", ""),
                    "name":         item.get("stk_nm", ""),
                    "qty":          int(item.get("rmnd_qty", 0)),
                    "avg_price":    float(item.get("avg_prc", 0)),
                    "current_price": float(item.get("cur_prc", 0)),
                    "eval_amount":  float(item.get("evlt_amt", 0)),
                    "eval_profit":  float(item.get("evlt_pfls", 0)),
                    "profit_rate":  float(item.get("pfls_rt", 0)),
                })

            return {
                "holdings":         holdings,
                "cash":             float(data.get("dnca_tot_amt", 0)),
                "total_eval_amount": float(data.get("tot_evlt_amt", 0)),
                "total_profit":     float(data.get("tot_pfls", 0)),
            }
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return {"holdings": [], "cash": 0, "total_eval_amount": 0, "total_profit": 0}

    def get_unfilled_orders(self) -> list:
        """미체결 주문 조회 (kt00013)"""
        url = f"{self.base_url}/api/dostk/acnt"
        try:
            resp = requests.get(
                url,
                headers=self._headers("kt00013"),
                params={"acnt_no": self.account_no},
                timeout=10,
            )
            data = resp.json()
            return data.get("oso_ord_list", [])
        except Exception as e:
            logger.error(f"미체결 조회 실패: {e}")
            return []

    def ping(self) -> bool:
        """API 연결 확인 (토큰 발급 성공 여부)"""
        try:
            _ = self.token
            return True
        except Exception:
            return False
```

---

## 6-4. trading/order.py

```python
# trading/order.py
import logging
from trading.kiwoom_api import KiwoomRestClient
from config.settings import settings

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    리밸런싱 주문 실행기
    순서: ① 매도 → ② 잔고 확인 → ③ 매수
    """

    def __init__(self):
        self.api = KiwoomRestClient()
        self.cfg = settings.trading

    def execute_rebalancing(
        self,
        current_holdings: list[str],
        target_portfolio: list[str],
    ) -> tuple[list[str], list[str]]:
        """
        리밸런싱 주문 실행

        Args:
            current_holdings: 현재 보유 종목 코드 리스트
            target_portfolio:  신규 목표 포트폴리오 코드 리스트

        Returns:
            (매도 완료 리스트, 매수 완료 리스트)
        """
        # ⚠️ 모의투자는 KRX만 지원
        exchange = "KRX" if self.api.is_paper else "SOR"

        sell_list = [t for t in current_holdings if t not in target_portfolio]
        buy_list  = [t for t in target_portfolio if t not in current_holdings]
        logger.info(f"리밸런싱 계획: 매도 {len(sell_list)}개, 매수 {len(buy_list)}개")

        sell_done, buy_done = [], []

        # ① 매도 먼저 (예수금 확보)
        balance = self.api.get_balance()
        for ticker in sell_list:
            holding = next(
                (h for h in balance["holdings"] if h["ticker"] == ticker), None
            )
            if holding and holding["qty"] > 0:
                result = self.api.sell_stock(
                    ticker=ticker,
                    qty=holding["qty"],
                    order_type="3",       # 시장가
                    exchange=exchange,
                )
                if result.get("return_code") == 0:
                    sell_done.append(ticker)

        # ② 예수금 재확인
        updated_balance = self.api.get_balance()
        # 99% 사용 (잔여 수수료·세금 여유)
        available_cash = updated_balance.get("cash", 0) * 0.99
        n_buy = len(buy_list)

        if n_buy == 0:
            return sell_done, buy_done

        budget_per_stock = available_cash / n_buy

        # ③ 매수 (동일 비중)
        for ticker in buy_list:
            price_data = self.api.get_current_price(ticker)
            price = price_data.get("current_price", 0)
            if price <= 0:
                logger.warning(f"현재가 조회 실패, 매수 스킵: {ticker}")
                continue

            # 수수료 감안 매수 가능 수량
            qty = int(budget_per_stock / (price * (1 + self.cfg.commission_rate)))
            if qty <= 0:
                logger.warning(f"예산 부족, 매수 스킵: {ticker} (가격: {price:,}원)")
                continue

            result = self.api.buy_stock(
                ticker=ticker,
                qty=qty,
                order_type="3",
                exchange=exchange,
            )
            if result.get("return_code") == 0:
                buy_done.append(ticker)

        logger.info(
            f"리밸런싱 완료 — 매도: {len(sell_done)}/{len(sell_list)}, "
            f"매수: {len(buy_done)}/{len(buy_list)}"
        )
        return sell_done, buy_done
```

---

## 6-5. 모의투자 테스트 순서

```bash
# 1. 토큰 발급 테스트
python -c "
from trading.kiwoom_api import KiwoomRestClient
client = KiwoomRestClient()
print('토큰:', client.token[:20], '...')
print('연결 OK:', client.ping())
"

# 2. 현재가 조회 테스트 (삼성전자)
python -c "
from trading.kiwoom_api import KiwoomRestClient
client = KiwoomRestClient()
print(client.get_current_price('005930'))
"

# 3. 잔고 조회 테스트
python -c "
from trading.kiwoom_api import KiwoomRestClient
client = KiwoomRestClient()
bal = client.get_balance()
print(f'예수금: {bal[\"cash\"]:,}원')
print(f'보유종목: {len(bal[\"holdings\"])}개')
"

# 4. 모의투자 매수 테스트 (삼성전자 1주, 시장가)
python -c "
from trading.kiwoom_api import KiwoomRestClient
client = KiwoomRestClient()
# IS_PAPER_TRADING=True 확인 필수!
assert client.is_paper, '실전 환경! 중단'
result = client.buy_stock('005930', qty=1, order_type='3', exchange='KRX')
print(result)
"
```

---

## 6-6. 주의사항 요약

| 항목 | 내용 |
|------|------|
| IP 화이트리스트 | 등록된 IP에서만 요청 가능. 유동 IP 변경 시 포털에서 재등록 |
| 모의투자 제한 | `mockapi.kiwoom.com`은 KRX만 지원 → exchange='KRX' 고정 |
| 토큰 필드명 | `data["token"]` (data["access_token"] 없음) |
| 응답 필드 재확인 | `cur_prc`, `ord_no` 등은 PDF 명세서 기준으로 실제 확인 후 수정 |
| 알고리즘 등록 | API 사용 시 한국거래소 알고리즘 계좌 자동 등록 대상 (법적 의무 이행) |
