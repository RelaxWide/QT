"""
scheduler/exit_check.py
실행 시각: 평일 23:00~04:00 매시 KR (장 중 매시간)
역할: Phase 4 보유 포지션 손절·트레일 체크 → 즉시 청산

수동 실행:
    python scheduler/exit_check.py [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient
from live_trading.orders import OrderManager, _get_live_qty
from live_trading.tracker_live import load_live_positions, append_live_trade
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")


def main(dry_run: bool = False):
    guard = RiskGuard.from_config()
    # 청산은 daily_loss 도달해도 허용
    ok, reason = guard.check(allow_exits=True)
    if not ok:
        log.warning(f"[exit_check] 전면 정지: {reason}")
        return

    om = OrderManager.from_config()
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    from src.fetch.prices import fetch_all
    from src.fetch.universe import get_sp500_tickers
    from paper_trading.tracker import load_positions
    import pandas as pd

    positions = load_positions()
    if not positions:
        log.info("[exit_check] 보유 포지션 없음")
        return

    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    price_data = fetch_all(
        list(positions.keys()),
        cfg["data"]["start_date"],
        cfg["data"]["end_date"],
        min_bars=50,
    )

    exits = []
    for sym, pos in positions.items():
        df = price_data.get(sym)
        if df is None or today not in df.index:
            continue
        cur = float(df.at[today, "close"])

        # 손절 체크
        if cur <= pos.stop_current:
            log.info(f"[exit_check] {sym} 손절 — 현재가 ${cur:.2f} <= 손절 ${pos.stop_current:.2f}")
            exits.append((sym, cur))
            continue

        # 트레일 스탑 업데이트 (Donchian 최저값)
        trail_p = cfg["phase1_breakout_pullback"].get("trail_donchian_period", 20)
        if len(df) >= trail_p:
            trail_stop = float(df["low"].rolling(trail_p).min().iloc[-1])
            if trail_stop > pos.stop_current:
                pos.stop_current = trail_stop
                log.info(f"[exit_check] {sym} 트레일 스탑 → ${trail_stop:.2f}")

    # 청산 주문
    from paper_trading.tracker import save_positions
    save_positions(positions)

    for sym, cur_price in exits:
        qty = _get_live_qty("phase4", sym)
        if qty <= 0:
            log.warning(f"[exit_check] {sym}: 보유수량 0 — SELL 스킵")
            continue
        sell_price = round(cur_price * 0.995, 2)
        r = om._send("phase4", sym, str(today.date()), "SELL", qty, sell_price, dry_run)
        if r.ok:
            log.info(f"[exit_check] {sym} SELL {qty}주 @${sell_price:.2f} order_no={r.order_no}")
        else:
            log.error(f"[exit_check] {sym} SELL 실패: {r.error}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
