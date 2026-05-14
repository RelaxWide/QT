"""
scheduler/wednesday_morning_buy.py
실행 시각: 매주 목 00:00 KR (= Wed 11 AM ET 서머타임 / Wed 10 AM ET 표준시)
역할:
  Wed 06:00 KR 에 daily_close 가 저장한 매수 후보를 11 AM ET 정각에 KIS LIMIT 주문 전송.
  표준시(11월 초~3월)에는 시작 시 ET 10:00 → 1시간 슬립 후 11:00 ET 에 실행.

수동 실행:
    python scheduler/wednesday_morning_buy.py [--dry-run] [--no-wait]
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

WED_BUY_PENDING = Path("live_trading/wed_buy_pending.json")
TARGET_ET_HOUR  = 11   # Wed 11:00 ET = 가격 안정 시점


def _wait_until_target_et():
    """ET TARGET_ET_HOUR 까지 대기. 이미 지났으면 즉시 반환."""
    et      = ZoneInfo("America/New_York")
    now_et  = datetime.now(et)
    target  = now_et.replace(hour=TARGET_ET_HOUR, minute=0, second=0, microsecond=0)
    if now_et >= target:
        log.info(f"[wed_buy] 현재 ET {now_et.strftime('%H:%M')} - 즉시 실행")
        return
    wait = (target - now_et).total_seconds()
    log.info(f"[wed_buy] 현재 ET {now_et.strftime('%H:%M')} → {TARGET_ET_HOUR}:00 까지 {wait/60:.1f}분 대기")
    time.sleep(wait)
    log.info(f"[wed_buy] ET 11:00 도달 - 주문 실행")


def main(dry_run: bool = False, no_wait: bool = False):
    if not WED_BUY_PENDING.exists():
        log.info(f"[wed_buy] pending 파일 없음 - 매수 후보 없음, 종료")
        return

    payload = json.loads(WED_BUY_PENDING.read_text(encoding="utf-8"))
    if not payload:
        log.info(f"[wed_buy] pending 비어있음 - 종료")
        WED_BUY_PENDING.unlink()
        return

    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))

    # 리스크 가드 재확인
    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[wed_buy] 신규 주문 차단: {reason} - pending 삭제, 종료")
        WED_BUY_PENDING.unlink()
        return

    # ET 11:00 까지 대기
    if not no_wait:
        _wait_until_target_et()

    om = OrderManager.from_config(allow_prod=True)

    total_orders = []
    for strategy in ("clenow", "weinstein"):
        section = payload.get(strategy)
        if not section:
            continue
        signal_date = section["signal_date"]
        symbols     = section["symbols"]
        log.info(f"[wed_buy] {strategy} 매수 시작 - {len(symbols)} 종목 (signal_date={signal_date})")
        orders = om.place_buys_at_kis_price(strategy, symbols, signal_date, dry_run=dry_run)
        total_orders.extend(orders)
        log.info(f"[wed_buy] {strategy} 매수 주문 {len(orders)}건")

    # 처리 완료 - pending 삭제 (재실행 방지)
    if not dry_run:
        WED_BUY_PENDING.unlink()
        log.info(f"[wed_buy] pending 파일 삭제")

    # 텔레그램 알림
    if total_orders:
        lines = [f"🌅 Wed 11 AM ET 매수 실행 ({len(total_orders)}건)"]
        for r in total_orders:
            status = "OK" if r.ok else "FAIL"
            lines.append(f"  [{status}] {r.symbol} x{r.qty} @${r.price:.2f}")
        _notify("\n".join(lines), cfg_live)

    log.info("[wed_buy] 완료")


def _notify(msg: str, cfg_live: dict):
    try:
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="실제 주문 없이 시뮬레이션")
    p.add_argument("--no-wait", action="store_true", help="ET 11:00 대기 없이 즉시 실행 (테스트용)")
    args = p.parse_args()
    main(dry_run=args.dry_run, no_wait=args.no_wait)
