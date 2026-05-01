"""
KIS 잔고 ↔ 로컬 positions_live 동기화

실행:
    python -m live_trading.account --sync
    python -m live_trading.account --report
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yaml

from live_trading.kis_client import KISClient
from live_trading.tracker_live import (
    LivePosition,
    load_live_positions,
    save_live_positions,
    append_slippage,
)

CONFIG_PATH = Path("config_live.yaml")
log = logging.getLogger("kis")

STRATEGIES = ("phase4", "clenow", "weinstein")


def sync_all(kis: KISClient, notify_fn=None) -> dict:
    """
    KIS 전체 잔고를 조회해 3전략 positions_live 파일과 비교·갱신.
    반환: {strategy: {"added": [...], "removed": [...], "mismatches": [...]}}
    """
    balance = kis.get_balance()
    kis_holdings: dict[str, dict] = {
        p["symbol"]: p for p in balance.get("positions", [])
    }

    report = {}
    for strategy in STRATEGIES:
        local = load_live_positions(strategy)
        result = _reconcile(strategy, local, kis_holdings, notify_fn)
        report[strategy] = result

    return report


def _reconcile(
    strategy: str,
    local: dict[str, LivePosition],
    kis_all: dict[str, dict],
    notify_fn=None,
) -> dict:
    """
    로컬 positions_live_{strategy}.json 과 KIS 잔고를 비교.

    케이스:
    A. 로컬 O / KIS O → qty 불일치 시 경고
    B. 로컬 O / KIS X → 외부 청산 또는 미체결 → 경고
    C. 로컬 X / KIS O → 수동 매수 또는 미기록 → 경고
    """
    added      = []
    removed    = []
    mismatches = []

    local_syms = set(local.keys())
    kis_syms   = set(kis_all.keys())

    # B: 로컬에 있는데 KIS에 없음
    for sym in local_syms - kis_syms:
        msg = f"[{strategy}] {sym}: 로컬 보유 O / KIS 잔고 X — 외부 청산 또는 미체결 가능성"
        log.warning(msg)
        if notify_fn:
            notify_fn(msg)
        removed.append(sym)

    # C: KIS에 있는데 로컬에 없음
    for sym in kis_syms - local_syms:
        kis_qty = kis_all[sym]["qty"]
        msg = f"[{strategy}] {sym}: KIS 보유 {kis_qty}주 / 로컬 X — 수동 매수 또는 미기록"
        log.warning(msg)
        if notify_fn:
            notify_fn(msg)
        added.append(sym)

    # A: 양쪽 모두 있지만 수량 불일치
    for sym in local_syms & kis_syms:
        local_qty = local[sym].qty
        kis_qty   = kis_all[sym]["qty"]
        if local_qty != kis_qty:
            msg = (f"[{strategy}] {sym}: 수량 불일치 — "
                   f"로컬 {local_qty}주 / KIS {kis_qty}주")
            log.warning(msg)
            if notify_fn:
                notify_fn(msg)
            mismatches.append({"symbol": sym, "local_qty": local_qty, "kis_qty": kis_qty})
            # KIS 실제값으로 로컬 업데이트
            local[sym].qty = kis_qty

    save_live_positions(strategy, local)
    return {"added": added, "removed": removed, "mismatches": mismatches}


def record_fill(
    strategy: str,
    symbol: str,
    signal_price: float,
    fill_price: float,
    qty: int,
    order_no: str,
    entry_date: str,
    fill_date: str,
) -> None:
    """
    체결 후 positions_live 기록 + slippage_log 업데이트.
    orders.py의 BUY 체결 확인 후 호출한다.
    """
    positions = load_live_positions(strategy)

    # 신규 포지션 저장
    positions[symbol] = LivePosition(
        symbol=symbol,
        strategy=strategy,
        entry_date=entry_date,
        fill_date=fill_date,
        signal_price=signal_price,
        fill_price=fill_price,
        qty=qty,
        order_no=order_no,
    )
    save_live_positions(strategy, positions)

    # 슬리피지 기록
    slippage_pct = (fill_price - signal_price) / signal_price * 100 if signal_price else 0
    append_slippage({
        "date":          fill_date,
        "strategy":      strategy,
        "symbol":        symbol,
        "signal_price":  round(signal_price, 4),
        "fill_price":    round(fill_price, 4),
        "slippage_pct":  round(slippage_pct, 4),
        "qty":           qty,
        "order_no":      order_no,
    })
    log.info(f"[account] {strategy} {symbol} fill 기록 — 슬리피지 {slippage_pct:+.3f}%")


def slippage_report() -> dict:
    """slippage_log.csv 요약 통계."""
    from live_trading.tracker_live import SLIPPAGE_LOG
    if not SLIPPAGE_LOG.exists() or SLIPPAGE_LOG.stat().st_size == 0:
        return {"records": 0}
    df = pd.read_csv(SLIPPAGE_LOG)
    if df.empty:
        return {"records": 0}
    return {
        "records":      len(df),
        "mean_pct":     round(df["slippage_pct"].mean(), 4),
        "std_pct":      round(df["slippage_pct"].std(), 4),
        "max_pct":      round(df["slippage_pct"].max(), 4),
        "min_pct":      round(df["slippage_pct"].min(), 4),
        "by_strategy":  df.groupby("strategy")["slippage_pct"].mean().round(4).to_dict(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────
def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--sync",   action="store_true", help="KIS 잔고 ↔ 로컬 동기화")
    p.add_argument("--report", action="store_true", help="슬리피지 리포트 출력")
    args = p.parse_args()

    if args.sync:
        kis = KISClient.from_config()
        report = sync_all(kis)
        for strategy, r in report.items():
            print(f"\n[{strategy}]")
            if r["added"]:
                print(f"  KIS에만 있음 (미기록): {r['added']}")
            if r["removed"]:
                print(f"  로컬에만 있음 (외부청산?): {r['removed']}")
            if r["mismatches"]:
                for m in r["mismatches"]:
                    print(f"  수량 불일치: {m['symbol']} 로컬={m['local_qty']} KIS={m['kis_qty']}")
            if not any(r.values()):
                print("  [OK] 일치")

    if args.report:
        r = slippage_report()
        if r["records"] == 0:
            print("슬리피지 데이터 없음 (체결 기록 후 확인 가능)")
        else:
            print(f"체결 건수:   {r['records']}")
            print(f"평균 슬리피지: {r['mean_pct']:+.4f}%")
            print(f"표준편차:    {r['std_pct']:.4f}%")
            print(f"최대:        {r['max_pct']:+.4f}%")
            print(f"최소:        {r['min_pct']:+.4f}%")
            print(f"전략별 평균: {r['by_strategy']}")


if __name__ == "__main__":
    _cli()
