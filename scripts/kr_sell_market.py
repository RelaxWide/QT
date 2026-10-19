"""
KR 보유 종목 시장가 정리.

미체결 지정가 매도가 남아 있으면 그 주문을 시장가로 정정하고(새 주문은 수량이 묶여 거부됨),
매도 주문이 없는 보유분은 시장가 신규 매도. 거래정지 종목은 KIS 가 거부 → 결과에 표시.

사용:
    python scripts/kr_sell_market.py            # 미리보기 (조회만, 주문 없음)
    python scripts/kr_sell_market.py --execute  # 실제 실행
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import yaml

from live_trading.kis_client import KISClient


def main(execute: bool):
    cfg = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    kis = KISClient(cfg, allow_prod=True, market="kr")

    bal = kis.get_balance()
    if bal.get("_failed"):
        sys.exit("잔고 조회 실패 — 중단")
    held = {p["symbol"]: int(p["qty"]) for p in bal["positions"]}
    opens = [o for o in kis.get_open_orders_kr() if o["side"] == "SELL" and o["open_qty"] > 0]
    open_qty = {}
    for o in opens:
        open_qty[o["symbol"]] = open_qty.get(o["symbol"], 0) + o["open_qty"]

    print(f"보유 {len(held)}종목: {held}")
    for o in opens:
        print(f"  미체결 매도 {o['symbol']} {o['open_qty']}주 @{o['price']:,.0f} → 시장가 정정")
    fresh = {s: q - open_qty.get(s, 0) for s, q in held.items() if q - open_qty.get(s, 0) > 0}
    for s, q in fresh.items():
        print(f"  주문 없는 보유 {s} {q}주 → 시장가 신규 매도")
    if not execute:
        print("\n미리보기 — 실행하려면 --execute")
        return

    for o in opens:
        r = kis.revise_order_to_market_kr(o["orgno"], o["order_no"])
        print(f"{'OK ' if r['order_no'] else 'FAIL'} 정정 {o['symbol']}: {r['raw'].get('msg1', '')}")
        time.sleep(0.5)
    for s, q in fresh.items():
        r = kis.place_order(s, q, side="SELL", order_type="MARKET")
        print(f"{'OK ' if r['order_no'] else 'FAIL'} 신규 {s} {q}주: {r['raw'].get('msg1', '')}")
        time.sleep(0.5)
    print("\n완료 — 체결 여부는 잔고로 확인할 것 (접수 ≠ 체결)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true")
    main(p.parse_args().execute)
