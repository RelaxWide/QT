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


def sync_all(kis: KISClient) -> dict:
    """
    KIS 전체 잔고를 조회해 **계정 단위**로 로컬 추적과 대조.

    KIS 잔고는 종합계좌 단위라 종목이 어느 전략 소속인지 구분하지 않는다.
    따라서 전 전략(phase4/clenow/weinstein) positions_live 의 **합집합**과 비교해야
    "Clenow 가 산 종목이 phase4·weinstein 에는 없다"는 거짓 경고를 피한다.

    반환:
        {
          "untracked": [sym, ...],        # KIS 보유 O / 어느 전략에도 X → 미할당 (등록 필요)
          "missing":   [(sym, strat), ...],# 전략 보유 O / KIS X → 외부 청산·미체결 의심
          "mismatch":  [{symbol, strategy, local_qty, kis_qty}, ...],
          "kis_count": int,
        }
    """
    balance = kis.get_balance()
    kis_holdings: dict[str, dict] = {
        p["symbol"]: p for p in balance.get("positions", [])
    }
    kis_syms = set(kis_holdings)

    # 전 전략 합집합: symbol -> {strategy: qty}
    tracked: dict[str, dict[str, int]] = {}
    for strategy in STRATEGIES:
        for sym, pos in load_live_positions(strategy).items():
            tracked.setdefault(sym, {})[strategy] = pos.qty
    tracked_syms = set(tracked)

    # 미할당: KIS 에 있는데 어느 전략에도 없음 (진짜 등록 필요 케이스)
    untracked = sorted(kis_syms - tracked_syms)

    # 외부 청산·미체결: 전략이 추적 중인데 KIS 잔고에 없음
    missing = sorted(
        (sym, strat)
        for sym in tracked_syms - kis_syms
        for strat in tracked[sym]
    )

    # 수량 불일치: 계정 단위 합계 vs KIS 실제
    mismatch = []
    for sym in kis_syms & tracked_syms:
        kis_qty     = int(kis_holdings[sym]["qty"])
        local_total = sum(tracked[sym].values())
        if local_total != kis_qty:
            for strat, q in tracked[sym].items():
                mismatch.append({
                    "symbol": sym, "strategy": strat,
                    "local_qty": q, "kis_qty": kis_qty,
                })

    if untracked:
        log.warning(f"[sync] 미할당 KIS 보유 (등록 필요): {untracked}")
    for sym, strat in missing:
        log.warning(f"[sync] {strat} {sym}: 전략 보유 O / KIS X — 외부 청산·미체결 의심")
    for m in mismatch:
        log.warning(f"[sync] {m['strategy']} {m['symbol']}: 수량 불일치 "
                     f"로컬 {m['local_qty']} / KIS {m['kis_qty']}")

    return {
        "untracked": untracked,
        "missing":   missing,
        "mismatch":  mismatch,
        "kis_count": len(kis_syms),
        "balance":   balance,
    }


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
        r = sync_all(kis)
        print(f"\nKIS 보유: {r['kis_count']}종목")
        if r["untracked"]:
            print(f"  미할당 (어느 전략에도 없음): {r['untracked']}")
        if r["missing"]:
            for sym, strat in r["missing"]:
                print(f"  로컬에만 있음 (외부청산?): {strat} {sym}")
        if r["mismatch"]:
            for m in r["mismatch"]:
                print(f"  수량 불일치: {m['strategy']} {m['symbol']} 로컬={m['local_qty']} KIS={m['kis_qty']}")
        if not (r["untracked"] or r["missing"] or r["mismatch"]):
            print("  [OK] 계정 단위 일치")

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
