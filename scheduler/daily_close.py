"""
scheduler/daily_close.py
실행 시각: 평일 06:00 KR (미국장 마감 직후)
역할:
  1. Clenow / Weinstein 신호 생성 → 즉시 주문
  2. Phase 4 신규 신호 생성 → pending.json 저장 (다음날 morning_entry가 주문)
  3. KIS 잔고 동기화

수동 실행:
    python scheduler/daily_close.py [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient
from live_trading.orders import OrderManager
from live_trading.account import sync_all
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")


def main(dry_run: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[daily_close] 신규 주문 차단: {reason}")
        # 청산 신호는 그래도 처리
        allow_new = False
    else:
        allow_new = True

    today      = pd.Timestamp.now().normalize()
    is_wed     = today.weekday() == 2

    # ── 1. 가격 데이터 로드 ──────────────────────────────────────────────
    from src.fetch.universe import get_sp500_tickers
    from src.fetch.prices import fetch_all
    tickers    = get_sp500_tickers()
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=150)
    log.info(f"[daily_close] {len(price_data)} 종목 로드 완료")

    om = OrderManager.from_config(allow_prod=True)

    # ── 2. Clenow 신호 → 주문 ────────────────────────────────────────────
    from paper_trading.live_signals import get_clenow_signals
    from paper_trading.simple_tracker import load_simple_positions
    cl_pos  = load_simple_positions("clenow")
    cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wed)

    # 청산 신호는 allow_new 무관하게 실행
    cl_sell = {k: v for k, v in cl_sigs.items() if "sell" in k}
    cl_buy  = {"buy": cl_sigs.get("buy", [])} if allow_new else {"buy": []}
    cl_orders = om.place_clenow_orders({**cl_sell, **cl_buy}, price_data, today, dry_run)
    log.info(f"[daily_close] Clenow 주문 {len(cl_orders)}건")

    # ── 3. Weinstein 신호 → 주문 ─────────────────────────────────────────
    from paper_trading.live_signals import get_weinstein_signals
    w_pos  = load_simple_positions("weinstein")
    w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wed)

    w_sell = {k: v for k, v in w_sigs.items() if "sell" in k}
    w_buy  = {"buy": w_sigs.get("buy", [])} if allow_new else {"buy": []}
    w_orders = om.place_weinstein_orders({**w_sell, **w_buy}, price_data, today, dry_run)
    log.info(f"[daily_close] Weinstein 주문 {len(w_orders)}건")

    # ── 4. Phase 4 신규 신호 → pending.json 저장 (익일 MOO) ─────────────
    if allow_new:
        _update_phase4_pending(cfg, cfg_live, price_data, today)

    # ── 5. KIS 잔고 동기화 ───────────────────────────────────────────────
    if not dry_run:
        kis = KISClient.from_config(allow_prod=True)
        report = sync_all(kis, notify_fn=lambda msg: _notify(msg, cfg_live))
        for strategy, r in report.items():
            if any(r.values()):
                log.warning(f"[sync] {strategy}: {r}")

    log.info("[daily_close] 완료")


def _update_phase4_pending(cfg, cfg_live, price_data, today):
    """Phase 4 신호 재생성 → pending.json 업데이트."""
    from src.indicators.regime import compute_regime
    from src.indicators.factors import build_factor_matrices
    from src.strategy.factor_stack import generate_factor_signals
    from paper_trading.tracker import load_positions, save_pending, PendingEntry

    p1 = cfg["phase1_breakout_pullback"]
    p2 = cfg["phase2_cloud_support"]
    p3 = cfg["phase3_hybrid"]
    p4 = cfg["phase4_factor_stack"]
    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
    p4["momentum_period"] = p4.get("momentum_period", 63)

    regime_on = compute_regime(
        cfg["data"]["start_date"],
        cfg["data"]["end_date"],
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )
    if today in regime_on.index and not bool(regime_on.at[today, "trade_ok"]):
        log.info("[phase4] 레짐 OFF — pending 업데이트 스킵")
        return

    mom_period = p4.get("momentum_period", 63)
    mom_rank, bbw_rank, spy_mom = build_factor_matrices(price_data, mom_period=mom_period)
    current_positions = load_positions()
    new_pending = []

    for sym, df in price_data.items():
        if sym in current_positions:
            continue
        sigs = generate_factor_signals(sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom)
        for s in sigs:
            if s.entry_date == today + pd.Timedelta(days=1):
                new_pending.append(PendingEntry(
                    symbol=sym,
                    signal_date=str(today.date()),
                    entry_price_est=float(df.at[today, "close"]) if today in df.index else s.entry_price,
                    stop=s.stop,
                    r=s.r,
                    targets=s.targets,
                    partial_weights=s.partial_weights,
                ))

    save_pending(new_pending)
    log.info(f"[phase4] pending {len(new_pending)}건 저장")


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
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
