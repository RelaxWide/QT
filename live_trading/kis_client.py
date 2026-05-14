"""
한국투자증권(KIS) REST API 클라이언트 — 미국 주식 자동매매용

기본 사용:
    from live_trading.kis_client import KISClient
    cli = KISClient.from_config()
    print(cli.get_balance())
    print(cli.get_price("AAPL"))
    cli.place_order("AAPL", qty=1, side="BUY", order_type="LIMIT", price=180.0)

CLI 검증:
    python -m live_trading.kis_client --test-auth
    python -m live_trading.kis_client --test-balance
    python -m live_trading.kis_client --test-price AAPL
    python -m live_trading.kis_client --test-order AAPL 1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import yaml


CONFIG_PATH    = Path(__file__).parent.parent / "config_live.yaml"
TOKEN_CACHE    = Path(__file__).parent / ".kis_token.json"
TOKEN_HISTORY  = Path(__file__).parent / ".kis_last_issued.txt"   # 캐시 삭제와 무관하게 발급 시각 보존
MIN_REISSUE_HOURS = 2.0   # 최근 N시간 이내 발급된 적 있으면 신규 발급 거부
LOG_DIR        = Path(__file__).parent.parent / "logs"


# ── 로거 설정 ──────────────────────────────────────────────────────────────
def _build_logger() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("kis")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_DIR / f"kis_api_{datetime.now():%Y%m%d}.log",
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = _build_logger()


# ── 토큰 캐시 ──────────────────────────────────────────────────────────────
@dataclass
class Token:
    access_token: str
    expires_at:   str   # ISO datetime
    mode:         str = "mock"
    issued_at:    str = ""   # ISO datetime; 빈 문자열이면 expires_at - 24h 로 추정 (구 캐시 호환)

    def is_expired(self, margin_min: int = 60) -> bool:
        exp = datetime.fromisoformat(self.expires_at)
        return datetime.now() >= exp - timedelta(minutes=margin_min)

    def issued_datetime(self) -> datetime:
        if self.issued_at:
            try:
                return datetime.fromisoformat(self.issued_at)
            except Exception:
                pass
        return datetime.fromisoformat(self.expires_at) - timedelta(hours=24)


def _load_token() -> Token | None:
    if not TOKEN_CACHE.exists():
        return None
    try:
        return Token(**json.loads(TOKEN_CACHE.read_text(encoding="utf-8")))
    except Exception:
        return None


def _save_token(t: Token) -> None:
    TOKEN_CACHE.write_text(json.dumps(asdict(t), indent=2), encoding="utf-8")


def _read_last_issued() -> datetime | None:
    """캐시 파일과 별개로 유지되는 발급 이력 — 캐시 삭제 후에도 가드 동작."""
    if not TOKEN_HISTORY.exists():
        return None
    try:
        return datetime.fromisoformat(TOKEN_HISTORY.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_last_issued(t: datetime) -> None:
    TOKEN_HISTORY.write_text(t.isoformat(), encoding="utf-8")


# ── 거래코드(tr_id) 테이블 ────────────────────────────────────────────────
# 미국 주식: V*** = 모의(VTS), T*** = 실(prod)
TR_IDS = {
    "mock": {
        "buy":      "VTTT1002U",   # 미국 매수
        "sell":     "VTTT1001U",   # 미국 매도
        "balance":  "VTTS3012R",   # 잔고
        "psamount": "VTTS3007R",   # 매수가능금액
        "ccnl":     "VTTS3035R",   # 체결내역
        "cancel":   "VTTT1004U",   # 정정·취소
        "price":    "HHDFS00000300",  # 현재가 (모의/실 동일)
    },
    "prod": {
        "buy":      "TTTT1002U",
        "sell":     "TTTT1001U",
        "balance":  "TTTS3012R",
        "psamount": "TTTS3007R",
        "ccnl":     "TTTS3035R",
        "cancel":   "TTTT1004U",
        "price":    "HHDFS00000300",
    },
}

# 거래소 코드 — KIS는 시세 API와 거래 API에서 코드 체계가 다름
# 시세 (EXCD, 3자리): NAS / NYS / AMS
# 거래 (OVRS_EXCG_CD, 4자리): NASD / NYSE / AMEX
PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}


# ── 메인 클라이언트 ────────────────────────────────────────────────────────
class KISClient:
    def __init__(self, cfg: dict, allow_prod: bool = False):
        kis = cfg["kis"]
        self.mode        = kis["mode"]
        if self.mode not in ("mock", "prod"):
            raise ValueError(f"kis.mode must be 'mock' or 'prod', got {self.mode!r}")
        if self.mode == "prod" and not allow_prod:
            raise RuntimeError(
                "⚠️  실계좌(prod) 모드 진입 차단 — "
                "KISClient(cfg, allow_prod=True) 명시적으로 호출하세요. "
                "(Phase F 모의투자 4주 검증 통과 후에만 사용)"
            )
        self.app_key     = kis["app_key"]
        self.app_secret  = kis["app_secret"]
        self.base_url    = kis["base_url_mock"] if self.mode == "mock" else kis["base_url_prod"]
        acct             = kis["mock_account_no"] if self.mode == "mock" else kis["prod_account_no"]
        if acct == "12345678-01":
            raise ValueError(
                f"{self.mode}_account_no 가 placeholder('12345678-01') 입니다. "
                f"config_live.yaml에 실제 계좌번호를 입력하세요."
            )
        # KIS는 8자리-2자리(예: 12345678-01) 형식 → CANO/ACNT_PRDT_CD 분리
        self.cano        = acct.split("-")[0]
        self.acnt_prdt   = acct.split("-")[1] if "-" in acct else "01"
        self.exchange    = kis.get("exchange", "NASD")
        self.tr          = TR_IDS[self.mode]
        self._token: Token | None = _load_token()

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH, allow_prod: bool = False) -> "KISClient":
        if not path.exists():
            raise FileNotFoundError(
                f"{path} 가 없습니다. config_live.example.yaml 을 복사·수정하세요."
            )
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(cfg, allow_prod=allow_prod)

    # ── 인증 ────────────────────────────────────────────────────────────
    def _ensure_token(self) -> str:
        if self._token and not self._token.is_expired() and self._token.mode == self.mode:
            return self._token.access_token

        # KIS '1일 1회 발급 원칙' 가드 — 최근 N시간 내 발급 이력 있으면 거부
        now = datetime.now()
        last_issued_candidates = []
        if self._token and self._token.mode == self.mode:
            try:
                last_issued_candidates.append(self._token.issued_datetime())
            except Exception:
                pass
        hist = _read_last_issued()
        if hist is not None:
            last_issued_candidates.append(hist)

        if last_issued_candidates:
            last_issued = max(last_issued_candidates)
            elapsed = now - last_issued
            if elapsed < timedelta(hours=MIN_REISSUE_HOURS):
                minutes_ago = elapsed.total_seconds() / 60
                wait_min    = MIN_REISSUE_HOURS * 60 - minutes_ago
                raise RuntimeError(
                    f"[auth] 토큰 발급 가드 발동 - 최근 {minutes_ago:.0f}분 전 발급 이력 존재. "
                    f"KIS '1일 1회 발급 원칙' 보호로 신규 발급 차단. "
                    f"{wait_min:.0f}분 후 재시도하거나, 강제 발급이 필요하면 "
                    f"live_trading/.kis_token.json + live_trading/.kis_last_issued.txt 모두 삭제."
                )

        log.info(f"[auth] 새 토큰 발급 (mode={self.mode})")
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey":     self.app_key,
            "appsecret":  self.app_secret,
        }
        r = requests.post(url, json=body, timeout=10)
        if r.status_code != 200:
            log.error(f"[auth] HTTP {r.status_code}: {r.text}")
            raise RuntimeError(
                f"KIS 토큰 발급 실패 ({r.status_code}). "
                f"확인사항:\n"
                f"  1) mode={self.mode} 와 AppKey/AppSecret 환경 일치 여부 (모의/실전 키 다름)\n"
                f"  2) AppKey/AppSecret 오타·공백\n"
                f"  3) 모의투자 신청 완료 여부 (mode=mock인 경우)\n"
                f"응답: {r.text}"
            )
        d = r.json()
        # KIS는 expires_in 초 단위 반환 (보통 86400 = 24h)
        issued = datetime.now()
        exp    = issued + timedelta(seconds=int(d.get("expires_in", 86400)))
        self._token = Token(
            access_token=d["access_token"],
            expires_at=exp.isoformat(),
            mode=self.mode,
            issued_at=issued.isoformat(),
        )
        _save_token(self._token)
        _write_last_issued(issued)
        log.info(f"[auth] 토큰 발급 완료, 만료 {exp:%Y-%m-%d %H:%M}")
        return self._token.access_token

    def _headers(self, tr_id: str, custtype: str = "P") -> dict:
        return {
            "content-type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey":        self.app_key,
            "appsecret":     self.app_secret,
            "tr_id":         tr_id,
            "custtype":      custtype,
        }

    def _hashkey(self, body: dict) -> str:
        """KIS POST 주문 필수 — hashkey 헤더 값 발급."""
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "appkey":       self.app_key,
            "appsecret":    self.app_secret,
        }
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        r.raise_for_status()
        return r.json().get("HASH", "")

    # ── 시세 조회 ───────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> dict:
        """미국 주식 현재가 조회. 반환: {symbol, last, open, high, low, prev_close, volume}"""
        url    = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        params = {
            "AUTH":    "",
            "EXCD":    PRICE_EXCD.get(self.exchange, self.exchange),
            "SYMB":    symbol,
        }
        r = requests.get(url, headers=self._headers(self.tr["price"]),
                         params=params, timeout=10)
        r.raise_for_status()
        d = r.json()
        if d.get("rt_cd") != "0":
            log.error(f"[price] {symbol} 조회 실패: {d.get('msg1')}")
            return {}
        out = d.get("output", {})
        return {
            "symbol":     symbol,
            "last":       float(out.get("last", 0) or 0),
            "open":       float(out.get("open", 0) or 0),
            "high":       float(out.get("high", 0) or 0),
            "low":        float(out.get("low", 0) or 0),
            "prev_close": float(out.get("base", 0) or 0),
            "volume":     int(float(out.get("tvol", 0) or 0)),
        }

    # ── 잔고 조회 ───────────────────────────────────────────────────────
    def get_balance(self) -> dict:
        """
        미국 주식 잔고 + 매수가능금액.
        반환: {cash_usd, positions: [...], total_pnl_usd, fx_rate}
        cash_usd 는 inquire-psamount 의 ord_psbl_frcr_amt (실주문가능 USD).
        """
        url    = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        params = {
            "CANO":          self.cano,
            "ACNT_PRDT_CD":  self.acnt_prdt,
            "OVRS_EXCG_CD":  self.exchange,
            "TR_CRCY_CD":    "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        r = requests.get(url, headers=self._headers(self.tr["balance"]),
                         params=params, timeout=10)
        if not r.ok:
            log.error(f"[balance] HTTP {r.status_code}: {r.text}")
        r.raise_for_status()
        d = r.json()
        if d.get("rt_cd") != "0":
            log.error(f"[balance] 실패: {d.get('msg1')}")
            return {"cash_usd": 0.0, "positions": [], "total_pnl_usd": 0.0, "fx_rate": 0.0}

        positions = []
        for it in d.get("output1", []):
            qty = int(float(it.get("ovrs_cblc_qty", 0) or 0))
            if qty == 0:
                continue
            positions.append({
                "symbol":    it.get("ovrs_pdno"),
                "qty":       qty,
                "avg_price": float(it.get("pchs_avg_pric", 0) or 0),
                "cur_price": float(it.get("now_pric2", 0) or 0),
                "eval_amt":  float(it.get("ovrs_stck_evlu_amt", 0) or 0),
            })
        out2 = d.get("output2", {})

        # 매수가능금액 조회 (inquire-psamount): KIS 가 USD 잔고를
        # frcr_dncl_amt_2 가 아니라 ord_psbl_frcr_amt 로 반환하는 경우 대응
        import time as _t; _t.sleep(0.5)   # 초당 호출 한도 회피
        cash_usd, fx = self._get_psamount()

        # KIS 응답 필드 의미:
        #   ovrs_tot_pfls         = 해외총손익 (실제 P&L)
        #   tot_evlu_pfls_amt     = 총평가금액 (이름은 'pfls' 지만 실제로는 평가금액)
        #   frcr_pchs_amt1        = 외화매입금액 (cost basis)
        return {
            "cash_usd":       cash_usd,
            "total_pnl_usd":  float(out2.get("ovrs_tot_pfls", 0) or 0),
            "fx_rate":        fx,
            "positions":      positions,
        }

    def _get_psamount(self) -> tuple[float, float]:
        """해외주식 매수가능금액 조회. 반환: (orderable_usd, fx_rate). 초당 한도 시 1회 재시도."""
        import time as _t
        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
        params = {
            "CANO":            self.cano,
            "ACNT_PRDT_CD":    self.acnt_prdt,
            "OVRS_EXCG_CD":    self.exchange,
            "OVRS_ORD_UNPR":   "1",
            "ITEM_CD":         "AAPL",   # 임의 종목 (매수가능액 계산용)
        }
        for attempt in (1, 2):
            try:
                r = requests.get(url, headers=self._headers(self.tr["psamount"]),
                                 params=params, timeout=10)
                d = r.json() if r.ok else {}
                msg = d.get("msg1", "")
                if r.status_code in (429, 500) or "초당 거래건수" in msg:
                    if attempt == 1:
                        log.warning("[psamount] 초당 한도 - 1초 후 재시도")
                        _t.sleep(1.0)
                        continue
                r.raise_for_status()
                if d.get("rt_cd") != "0":
                    log.warning(f"[psamount] 실패: {msg}")
                    return 0.0, 0.0
                out = d.get("output", {}) or {}
                return (
                    float(out.get("ord_psbl_frcr_amt", 0) or 0),
                    float(out.get("exrt", 0) or 0),
                )
            except Exception as e:
                if attempt == 1:
                    log.warning(f"[psamount] 예외 - 재시도: {e}")
                    _t.sleep(1.0)
                    continue
                log.warning(f"[psamount] 최종 실패: {e}")
                return 0.0, 0.0
        return 0.0, 0.0

    # ── 주문 ───────────────────────────────────────────────────────────
    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,                     # "BUY" | "SELL"
        order_type: str = "LIMIT",     # "LIMIT" | "MARKET"
        price: float = 0.0,
    ) -> dict:
        """미국 주식 주문. 반환: {order_no, ksd_order_no, raw}"""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY|SELL, got {side}")
        tr_id = self.tr["buy"] if side == "BUY" else self.tr["sell"]

        # KIS 미국 주문 구분 코드:
        # ORD_DVSN: 00 = 지정가, 31~34 = LOO/LOC/MOO/MOC
        ord_dvsn = "00"
        ord_unpr = f"{price:.2f}"
        if order_type == "MARKET":
            # 시장가는 지원되지 않는 경우가 많아 LOO(31, 장개시지정가) 또는 자체 매핑
            # 실무에서는 현재가 +/- 1%로 LIMIT 주문 사용 권장
            ord_dvsn = "00"
            log.warning("[order] 미국주식은 MARKET 직접 미지원 — LIMIT으로 전환 필요")

        url  = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"
        body = {
            "CANO":          self.cano,
            "ACNT_PRDT_CD":  self.acnt_prdt,
            "OVRS_EXCG_CD":  self.exchange,
            "PDNO":          symbol,
            "ORD_QTY":       str(qty),
            "OVRS_ORD_UNPR": ord_unpr,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN":      ord_dvsn,
        }
        hashkey = self._hashkey(body)
        time.sleep(0.5)  # KIS rate limit: hashkey 호출 직후 주문 연속 시 EGW00201
        headers = self._headers(tr_id)
        headers["hashkey"] = hashkey
        log.info(f"[order] {side} {symbol} x{qty} @{ord_unpr} (tr_id={tr_id})")
        r = requests.post(url, headers=headers,
                          data=json.dumps(body), timeout=10)
        if not r.ok:
            log.error(f"[order] HTTP {r.status_code}: {r.text}")
            r.raise_for_status()
        d = r.json()
        if d.get("rt_cd") != "0":
            log.error(f"[order] 실패: {d.get('msg1')} / {d.get('msg_cd')}")
            return {"order_no": "", "raw": d}

        out = d.get("output", {})
        order_no = out.get("ODNO", "")
        log.info(f"[order] 성공 order_no={order_no}")
        return {
            "order_no":     order_no,
            "ksd_order_no": out.get("KRX_FWDG_ORD_ORGNO", ""),
            "raw":          d,
        }


# ── CLI ────────────────────────────────────────────────────────────────
def _cli():
    p = argparse.ArgumentParser()
    p.add_argument("--test-auth",    action="store_true", help="토큰 발급 테스트")
    p.add_argument("--test-balance", action="store_true", help="잔고 조회 테스트")
    p.add_argument("--test-price",   metavar="SYMBOL",    help="현재가 조회 테스트")
    p.add_argument("--test-order",   nargs=2, metavar=("SYMBOL", "QTY"),
                   help="지정가 매수 테스트 (현재가 -5% LIMIT)")
    args = p.parse_args()

    cli = KISClient.from_config(allow_prod=True)
    print(f"mode={cli.mode}, exchange={cli.exchange}, account={cli.cano}-{cli.acnt_prdt}")

    if args.test_auth:
        cli._ensure_token()
        print("✅ 인증 OK")

    if args.test_balance:
        b = cli.get_balance()
        print(f"예수금(USD): ${b['cash_usd']:,.2f}")
        print(f"평가손익:    ${b['total_pnl_usd']:,.2f}")
        print(f"보유 {len(b['positions'])}종목")
        for pos in b["positions"]:
            print(f"  {pos['symbol']:6s} {pos['qty']:>4d}주 @${pos['avg_price']:.2f} → ${pos['cur_price']:.2f}")

    if args.test_price:
        p = cli.get_price(args.test_price)
        print(p)

    if args.test_order:
        sym, qty = args.test_order[0], int(args.test_order[1])
        px = cli.get_price(sym)
        if not px:
            print("❌ 가격 조회 실패")
            return
        # 안전을 위해 현재가 -5% 지정가 (체결 안 될 가능성 ↑) → 모의투자 검증용
        order_px = round(px["last"] * 0.95, 2)
        print(f"테스트 매수: {sym} {qty}주 @${order_px} (현재가 ${px['last']})")
        r = cli.place_order(sym, qty, side="BUY", order_type="LIMIT", price=order_px)
        print(r)


if __name__ == "__main__":
    _cli()
