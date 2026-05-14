"""
KR KIS 잔고/매수가능금액 API 의 raw 응답을 덤프해서
어떤 필드에 데이터 있는지 확인.

사용:
    python scripts/debug_kis_balance_kr.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from live_trading.kis_client import KISClient


def main():
    kis = KISClient.from_config(allow_prod=True, market="kr")
    kis._ensure_token()

    print("=" * 70)
    print("1. KR inquire-balance (국내주식 잔고)")
    print("=" * 70)
    url = f"{kis.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
    params = {
        "CANO":            kis.cano,
        "ACNT_PRDT_CD":    kis.acnt_prdt,
        "AFHR_FLPR_YN":    "N",
        "OFL_YN":          "",
        "INQR_DVSN":       "02",
        "UNPR_DVSN":       "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN":       "01",
        "CTX_AREA_FK100":  "",
        "CTX_AREA_NK100":  "",
    }
    r = requests.get(url, headers=kis._headers(kis.tr["balance"]),
                     params=params, timeout=10)
    d = r.json()
    print(f"rt_cd={d.get('rt_cd')} msg={d.get('msg1')}")
    print()
    print("--- output1 (보유 종목) ---")
    for it in d.get("output1", []):
        try:
            qty = int(float(it.get("hldg_qty", 0) or 0))
        except Exception:
            qty = 0
        if qty == 0:
            continue
        print(f"  {it.get('pdno','').strip()}: qty={qty}, "
              f"avg={it.get('pchs_avg_pric')}원, cur={it.get('prpr')}원, "
              f"eval={it.get('evlu_amt')}원")
    print()
    print("--- output2 (잔고 요약, 0 아닌 필드만) ---")
    for entry in d.get("output2", []) or []:
        for k, v in entry.items():
            try:
                if float(v) != 0:
                    print(f"  {k}: {v}")
            except (ValueError, TypeError):
                if v:
                    print(f"  {k}: {v}")

    time.sleep(0.5)
    print()
    print("=" * 70)
    print("2. KR inquire-psbl-order (매수가능 현금)")
    print("=" * 70)
    cash = kis._get_psamount_kr("005930", ord_unpr=1)
    print(f"매수가능 현금: {cash:,.0f}원")


if __name__ == "__main__":
    main()
