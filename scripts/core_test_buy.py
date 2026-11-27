"""
scripts/core_test_buy.py — 코어(SPY) 1주 시험 매수 (API 주문 경로 점검용, 일회성)

수요일 정기 매수와 별개의 주문 키(core:SPY:<날짜>-test:BUY)를 쓰므로 그 주 정기 매수를 막지 않는다.
체결된 주식은 KIS 잔고의 SPY 로 잡혀 코어 보유로 그대로 인정된다 (12/3 정기 매수는 목표까지 남은 만큼만 산다).

    python scripts/core_test_buy.py            # 모의 실행 (주문 안 나감)
    python scripts/core_test_buy.py --live     # 실제 주문 (미국 정규장 시간에만)
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from scheduler._log_helper import setup_file_logger
setup_file_logger("core_test_buy")

from live_trading.orders import OrderManager
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")


def _notify(msg: str):
    try:
        cfg = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        n = cfg.get("notify", {})
        if n.get("telegram_token") and n.get("telegram_chat_id"):
            from src.notify import telegram
            telegram.send(msg, token=n["telegram_token"], chat_id=n["telegram_chat_id"])
    except Exception as e:
        log.warning(f"텔레그램 알림 실패: {e}")


def main(live: bool, qty: int):
    et = datetime.now(ZoneInfo("America/New_York"))
    if live and (et.weekday() >= 5 or not (9 * 60 + 35 <= et.hour * 60 + et.minute <= 15 * 60 + 50)):
        log.error(f"미국 정규장 시간이 아님 (ET {et:%a %H:%M}) — 주문 안 함")
        _notify(f"⚠️ <b>SPY 시험 매수 안 함</b>\n미국 정규장 시간이 아님 (ET {et:%a %H:%M})")
        return
    ok, reason = RiskGuard.from_config(market="us").check()
    if not ok:
        log.error(f"리스크 가드 차단: {reason}")
        _notify(f"🛑 <b>SPY 시험 매수 안 함</b>\n{reason}")
        return
    om = OrderManager.from_config(allow_prod=True)
    sym = om.cap.get("core_symbol", "SPY") if hasattr(om, "cap") else "SPY"
    cur = float(om.kis.get_price(sym).get("last", 0) or 0)
    if cur <= 0:
        log.error(f"{sym} 현재가 조회 실패")
        _notify(f"🚨 <b>{sym} 시험 매수 실패</b>\n현재가 조회 실패")
        return
    price = om._adjust_limit_price(cur * 1.005, "BUY")
    tag = f"{et:%Y-%m-%d}-test"

    def send():
        return om._send("core", sym, tag, "BUY", qty, price, dry_run=not live)

    if live:
        from live_trading.runlock import account_lock
        with account_lock("us", wait_sec=900):
            r = send()
    else:
        r = send()
    if not live:
        log.info(f"[DRY-RUN] {sym} {qty}주 @${price:.2f} 주문했을 것 (키 core:{sym}:{tag}:BUY)")
        return
    if getattr(r, "unknown", False):
        msg = f"🚨 <b>{sym} 시험 매수 접수 불명</b>\n{r.error}\n다음 06:30 장마감에서 KIS 잔고로 확인"
    elif r.ok:
        msg = f"✅ <b>{sym} 시험 매수 접수</b> {qty}주 @${price:.2f}\n주문번호 {r.order_no} · 체결은 06:30 장마감에서 확인"
    else:
        msg = f"🚨 <b>{sym} 시험 매수 실패</b>\n{r.error}"
    log.info(msg.replace("<b>", "").replace("</b>", ""))
    _notify(msg)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="실제 주문")
    p.add_argument("--qty", type=int, default=1)
    a = p.parse_args()
    main(a.live, a.qty)
