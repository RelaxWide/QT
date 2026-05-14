"""
scheduler/wednesday_morning_buy_kr.py
실행 시각: 매주 수요일 KST 09:00 (KOSPI 정규장 개장 직후, 시초가 +0.5% LIMIT 매수)
역할:
  화요일 16:00 KST 에 daily_close_kr 가 저장한 매수 후보를 즉시 LIMIT 매수.

수동:
    python scheduler/wednesday_morning_buy_kr.py [--dry-run] [--no-wait]

대기 모드:
    --no-wait 면 즉시 실행. 기본은 09:00 KST 까지 슬립.
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.orders import OrderManager
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING_KR = Path("live_trading/wed_buy_pending_kr.json")
TARGET_KST_HOUR    = 9   # KOSPI 정규장 개장
MARKET             = "kr"


def _wait_until_target_kst():
    kst    = ZoneInfo("Asia/Seoul")
    now    = datetime.now(kst)
    target = now.replace(hour=TARGET_KST_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        log.info(f"[wed_buy_kr] 현재 KST {now:%H:%M} - 즉시 실행")
        return
    wait = (target - now).total_seconds()
    log.info(f"[wed_buy_kr] 현재 KST {now:%H:%M} -> {TARGET_KST_HOUR}:00 까지 {wait/60:.1f}분 대기")
    time.sleep(wait)
    log.info(f"[wed_buy_kr] KST {TARGET_KST_HOUR}:00 도달 - 주문 실행")


def main(dry_run: bool = False, no_wait: bool = False):
    if not WED_BUY_PENDING_KR.exists():
        log.info(f"[wed_buy_kr] pending 파일 없음 - 매수 후보 없음, 종료")
        return

    payload = json.loads(WED_BUY_PENDING_KR.read_text(encoding="utf-8"))
    if not payload:
        log.info("[wed_buy_kr] pending 비어있음 - 종료")
        WED_BUY_PENDING_KR.unlink()
        return

    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))

    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[wed_buy_kr] 신규 주문 차단: {reason} - pending 삭제, 종료")
        WED_BUY_PENDING_KR.unlink()
        return

    if not no_wait:
        _wait_until_target_kst()

    om = OrderManager.from_config(allow_prod=True, market=MARKET)

    total_orders = []
    for strategy in ("clenow", "weinstein"):
        section = payload.get(strategy)
        if not section:
            continue
        signal_date = section["signal_date"]
        symbols     = section["symbols"]
        log.info(f"[wed_buy_kr] {strategy} 매수 시작 - {len(symbols)} 종목 (signal_date={signal_date})")
        orders = om.place_buys_at_kis_price(strategy, symbols, signal_date, dry_run=dry_run)
        total_orders.extend(orders)
        log.info(f"[wed_buy_kr] {strategy} 매수 주문 {len(orders)}건")

    if not dry_run:
        WED_BUY_PENDING_KR.unlink()
        log.info("[wed_buy_kr] pending 파일 삭제")

    if total_orders:
        lines = [f"🇰🇷 KR 수요일 09:00 매수 실행 ({len(total_orders)}건)"]
        for r in total_orders:
            status = "OK" if r.ok else "FAIL"
            lines.append(f"  [{status}] {r.symbol} x{r.qty} @₩{r.price:,.0f}")
        _notify("\n".join(lines), cfg_live)

    log.info("[wed_buy_kr] 완료")


def _notify(msg: str, cfg_live: dict):
    try:
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"[wed_buy_kr] 텔레그램 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-wait", action="store_true",
                   help="KST 09:00 대기 없이 즉시 실행 (테스트용)")
    args = p.parse_args()
    main(dry_run=args.dry_run, no_wait=args.no_wait)
