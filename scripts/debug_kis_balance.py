"""
KIS 잔고/매수가능금액 API 의 raw 응답을 덤프해서
USD 자산이 어떤 필드에 들어있는지 확인.

사용:
    python scripts/debug_kis_balance.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from live_trading.kis_client import KISClient


def main():
    kis = KISClient.from_config(allow_prod=True)
    token = kis._ensure_token()

    # ─── 1. 잔고 (inquire-balance) ────────────────────────────────────────
    print("=" * 70)
    print("1. inquire-balance (현재 코드가 사용하는 잔고 API)")
    print("=" * 70)
    url = f"{kis.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
    params = {
        "CANO":          kis.cano,
        "ACNT_PRDT_CD":  kis.acnt_prdt,
        "OVRS_EXCG_CD":  kis.exchange,
        "TR_CRCY_CD":    "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    r = requests.get(url, headers=kis._headers(kis.tr["balance"]),
                     params=params, timeout=10)
    d = r.json()
    print(f"rt_cd={d.get('rt_cd')} msg={d.get('msg1')}")
    print()
    print("--- output1 (보유 종목) ---")
    for it in d.get("output1", []):
        print(f"  {it.get('ovrs_pdno')}: qty={it.get('ovrs_cblc_qty')}, "
              f"avg=${it.get('pchs_avg_pric')}, cur=${it.get('now_pric2')}, "
              f"eval=${it.get('ovrs_stck_evlu_amt')}")
    print()
    print("--- output2 (잔고 요약, 0 아닌 필드만) ---")
    out2 = d.get("output2", {}) or {}
    for k, v in out2.items():
        try:
            if float(v) != 0:
                print(f"  {k}: {v}")
        except (ValueError, TypeError):
            if v:
                print(f"  {k}: {v}")

    # ─── 2. 매수가능금액 (inquire-psamount) ───────────────────────────────
    time.sleep(0.5)   # 초당 호출 한도 회피
    print()
    print("=" * 70)
    print("2. inquire-psamount (해외주식 매수가능금액 API)")
    print("=" * 70)
    url = f"{kis.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"
    # 매수가능금액 조회 TR_ID
    tr_id = "VTTS3007R" if kis.mode == "mock" else "TTTS3007R"
    headers = kis._headers(tr_id)
    # SPY 기준 매수가능금액 (종목 코드 필요)
    params = {
        "CANO":            kis.cano,
        "ACNT_PRDT_CD":    kis.acnt_prdt,
        "OVRS_EXCG_CD":    kis.exchange,
        "OVRS_ORD_UNPR":   "1",       # 1달러 기준
        "ITEM_CD":         "AAPL",    # 임의 종목 (가능금액 계산용)
    }
    r = requests.get(url, headers=headers, params=params, timeout=10)
    print(f"HTTP {r.status_code}")
    try:
        d = r.json()
        print(f"rt_cd={d.get('rt_cd')} msg={d.get('msg1')}")
        out = d.get("output", {}) or {}
        print("--- output (0 아닌 필드만) ---")
        for k, v in out.items():
            try:
                if float(v) != 0:
                    print(f"  {k}: {v}")
            except (ValueError, TypeError):
                if v:
                    print(f"  {k}: {v}")
    except Exception as e:
        print(f"파싱 실패: {e}")
        print(r.text[:500])


if __name__ == "__main__":
    main()
