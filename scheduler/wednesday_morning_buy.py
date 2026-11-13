"""
scheduler/wednesday_morning_buy.py
실행 시각: 매주 목 00:00 KR (= Wed 11 AM ET 서머타임 / Wed 10 AM ET 표준시 → 01:00 KR 에 주문)
역할:
  Wed 06:30 KR 에 daily_close 가 저장한 매수 후보를 11 AM ET 정각에 KIS LIMIT 주문 전송.
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

from scheduler._log_helper import setup_file_logger
setup_file_logger("wednesday_morning_buy")

from live_trading.orders import OrderManager
from live_trading.risk_guard import RiskGuard
from live_trading.freshness import expected_us_session

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


def main(dry_run: bool = False, no_wait: bool = False, allow_stale: bool = False):
    # ET 11:00 까지 대기 — 가드 확인·잠금보다 먼저 한다. 예전엔 가드를 확인한 뒤 최대 1시간을 기다려,
    # 그 사이에 KILL_SWITCH 를 켜거나 낙폭 한도에 닿아도 주문이 나갔다.
    if not no_wait:
        _wait_until_target_et()
    if dry_run:
        return _run(dry_run, allow_stale)
    # 장마감 처리·따라잡기와 겹치지 않게 계좌 잠금 (그쪽이 끝날 때까지 최대 15분 대기)
    from live_trading.runlock import account_lock, LockBusy
    try:
        with account_lock("us", wait_sec=900):
            return _run(dry_run, allow_stale)
    except LockBusy as e:
        log.error(f"[wed_buy] 실행 생략: {e}")
        cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        _notify(f"🚨 <b>US 수요일 매수 실행 못 함</b>\n{e}\n매수 후보는 남아 있음 — 수동 실행 필요", cfg_live)


def _signal_is_current(signal_date: str) -> tuple[bool, str]:
    """매수 후보가 '직전에 마감한 세션' 의 신호인가. 지연·수동 실행으로 며칠 지난 후보를 쓰지 않도록."""
    exp = expected_us_session()
    if exp is not None:
        want = str(exp.date())
        return signal_date[:10] == want, f"후보 신호일 {signal_date[:10]} ≠ 직전 마감 세션 {want}"
    age = (datetime.now(ZoneInfo("America/New_York")).date() - datetime.fromisoformat(signal_date[:10]).date()).days
    return age <= 4, f"후보 신호일 {signal_date[:10]} — {age}일 경과"


def _run(dry_run: bool, allow_stale: bool):
    # 전략 매수 후보가 없어도 코어(SPY) 보충은 확인한다
    payload = {}
    if WED_BUY_PENDING.exists():
        payload = json.loads(WED_BUY_PENDING.read_text(encoding="utf-8")) or {}
    else:
        log.info("[wed_buy] pending 파일 없음 - 전략 매수 후보 없음 (코어 보충만 확인)")

    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))

    # 리스크 가드 재확인
    guard = RiskGuard.from_config(market="us")
    ok, reason = guard.check()
    if not ok:
        if dry_run:
            log.warning(f"[DRY-RUN] 신규 주문 차단 상태: {reason} — 실제 실행이면 pending 을 지우고 "
                        f"중단 알림을 보낸다 (모의 실행이라 아무것도 건드리지 않음)")
            return
        log.warning(f"[wed_buy] 신규 주문 차단: {reason} - pending 삭제, 종료")
        if WED_BUY_PENDING.exists():
            WED_BUY_PENDING.unlink()
        _notify(f"🛑 <b>US 신규 매수 중단</b>\n{reason}\n"
                f"매수 후보 {sum(len(v.get('symbols', [])) for v in payload.values())}종목 건너뜀 "
                f"(보유 종목 청산은 계속 진행)", cfg_live)
        return

    om = OrderManager.from_config(allow_prod=True)

    total_orders = []
    stale_notes = []
    for strategy in ("clenow", "weinstein"):
        section = payload.get(strategy)
        if not section:
            continue
        signal_date = section["signal_date"]
        symbols     = section["symbols"]
        current, why = _signal_is_current(signal_date)
        if not current and not allow_stale:
            log.warning(f"[wed_buy] {strategy} 후보 건너뜀 — {why} (그래도 쓰려면 --allow-stale)")
            stale_notes.append(f"{strategy}: {why}")
            continue
        log.info(f"[wed_buy] {strategy} 매수 시작 - {len(symbols)} 종목 (signal_date={signal_date})")
        orders = om.place_buys_at_kis_price(strategy, symbols, signal_date, dry_run=dry_run,
                                            size_mult=section.get("size_mult") or None,
                                            sectors=section.get("sectors") or None,
                                            max_per_sector=section.get("max_per_sector"))
        total_orders.extend(orders)
        log.info(f"[wed_buy] {strategy} 매수 주문 {len(orders)}건")

    # 코어(SPY) — 목표 비중에 못 미치면 전략 매수 후 남는 현금으로 보충
    committed = sum(r.qty * r.price for r in total_orders if r.ok)
    # 코어 키는 주 단위 — 날짜 단위면 다른 날 재실행할 때마다 한 번씩 더 사서 "주 1회 분할 매수" 가 깨진다
    _iso = datetime.now(ZoneInfo("America/New_York")).isocalendar()
    core_date = f"{_iso[0]}-W{_iso[1]:02d}"
    core_orders = om.place_core_topup(core_date, dry_run=dry_run, cash_committed=committed)
    total_orders.extend(core_orders)
    log.info(f"[wed_buy] 코어 매수 주문 {len(core_orders)}건")

    # 처리 완료 - pending 삭제 (재실행 방지)
    if not dry_run and WED_BUY_PENDING.exists():
        WED_BUY_PENDING.unlink()
        log.info(f"[wed_buy] pending 파일 삭제")

    # 텔레그램 알림 (모의 실행은 로그만 — 실제 접수처럼 보이는 알림을 보내지 않는다)
    if dry_run:
        for r in total_orders:
            log.info(f"[DRY-RUN] 주문 예정: {r.side} {r.symbol} x{r.qty} @${r.price:.2f} (${r.qty * r.price:,.0f})")
        for note in stale_notes:
            log.info(f"[DRY-RUN] 지난 후보 건너뜀: {note}")
        log.info("[wed_buy] 모의 실행 완료 — 주문·파일 변경·알림 없음")
        return
    if stale_notes and not total_orders:
        _notify("⚠️ <b>US 매수 후보 건너뜀</b> (지난 신호)\n" + "\n".join(stale_notes), cfg_live)
    if total_orders:
        n_fail = sum(1 for r in total_orders if not r.ok)
        head = f"🌅 <b>US 매수 주문 접수</b> (수 11:00 ET) {len(total_orders) - n_fail}건"
        if n_fail:
            head += f" · 🚨 실패 {n_fail}건"
        lines = [head]
        for r in total_orders:
            status = "접수 불명" if getattr(r, "unknown", False) else "접수" if r.ok else "실패"
            lines.append(f"  [{status}] {r.symbol} x{r.qty} @${r.price:.2f} (${r.qty * r.price:,.0f})")
        for r in total_orders:
            if not r.ok and r.error:
                lines.append(f"  ↳ {r.symbol}: {r.error[:120]}")
        if any(getattr(r, "unknown", False) for r in total_orders):
            lines.append("🚨 접수 불명 = 응답 유실. 재전송하지 않음 — KIS 앱에서 접수 여부 확인")
        for note in stale_notes:
            lines.append(f"⚠️ 지난 후보 건너뜀 — {note}")
        if n_fail < len(total_orders):
            lines.append("체결 여부는 다음 장마감 처리(06:30 KST)에서 확정")
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
    p.add_argument("--allow-stale", action="store_true",
                   help="직전 세션 신호가 아닌 후보도 매수 (지연된 수동 실행을 의도한 경우만)")
    args = p.parse_args()
    main(dry_run=args.dry_run, no_wait=args.no_wait, allow_stale=args.allow_stale)
