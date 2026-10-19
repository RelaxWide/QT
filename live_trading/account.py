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
    append_live_trade,
    append_slippage,
)

CONFIG_PATH = Path("config_live.yaml")
log = logging.getLogger("kis")

STRATEGIES = ("phase4", "clenow", "weinstein")

# 접수 후 이 시간이 안 지난 주문은 아직 장이 안 돌았을 수 있으니 판정 보류
# (daily_close 수동 재실행 시 방금 낸 매도를 "미체결" 로 오판해 중복 주문하는 것 방지)
MIN_ORDER_AGE_HOURS = 4


def reconcile_fills(kis: KISClient, strategies=STRATEGIES) -> dict:
    """
    US pending 주문을 KIS 실잔고로 확정 (체결 여부 = 잔고, 체결가 = 체결내역 API).

    - pending_buy : 잔고에 있으면 open + 실체결가/수량 반영 + 슬리피지 기록,
                    없으면 미체결 만료로 보고 추적 삭제
    - pending_sell: 잔고에서 사라졌으면 trades_live 기록 후 삭제,
                    남아 있으면 open 으로 되돌려 재매도 대상(unfilled_sells)으로 반환
    잔고 조회 실패 시 예외를 그대로 올린다 — 빈 잔고를 "전량 체결" 로 오판하면 안 됨.

    반환: {buys_filled, buys_expired, sells_filled, sells_unfilled, skipped, price_fallback}
    """
    balance = kis.get_balance()
    if balance.get("_failed"):
        raise RuntimeError("KIS 잔고 조회 실패 — reconcile 중단 (포지션 변경 없음)")
    kis_pos = {p["symbol"]: p for p in balance.get("positions", [])}

    all_pos = {s: load_live_positions(s) for s in strategies}
    pendings = [(s, p) for s, ps in all_pos.items() for p in ps.values()
                if p.status in ("pending_buy", "pending_sell")]
    result = {"buys_filled": [], "buys_expired": [], "sells_filled": [],
              "sells_unfilled": [], "skipped": [], "price_fallback": []}
    if not pendings:
        return result

    # 체결가 — 실패해도 체결 여부 판정은 잔고로 가능하므로 폴백하고, 폴백 사실을 보고한다
    now = pd.Timestamp.now()
    fills: dict[str, dict] = {}
    try:
        start = (now - pd.Timedelta(days=10)).strftime("%Y%m%d")
        fills = kis.get_order_fills_us(start, now.strftime("%Y%m%d"))
    except Exception as e:
        log.warning(f"[reconcile] 체결내역 조회 실패 — 체결가 폴백 사용: {e}")

    # 한 종목을 여러 전략이 보유하면 계정 단위 잔고로 전략별 분리가 안 됨 → 수동 확인
    owners: dict[str, list[str]] = {}
    for s, ps in all_pos.items():
        for sym in ps:
            owners.setdefault(sym, []).append(s)

    today = now.date().isoformat()
    for strategy, pos in pendings:
        sym = pos.symbol
        tag = f"{strategy}/{sym}"
        if len(owners[sym]) > 1:
            result["skipped"].append(f"{tag} (복수 전략 보유)")
            continue
        try:
            age_h = (now - pd.Timestamp(pos.submitted_at)).total_seconds() / 3600
        except Exception:
            age_h = MIN_ORDER_AGE_HOURS   # 기록 없는 옛 포지션은 판정 진행
        if age_h < MIN_ORDER_AGE_HOURS:
            result["skipped"].append(f"{tag} (접수 {age_h:.1f}h 경과, 판정 보류)")
            continue

        held = int(kis_pos.get(sym, {}).get("qty", 0))
        positions = all_pos[strategy]

        if pos.status == "pending_buy":
            if held <= 0:
                del positions[sym]
                result["buys_expired"].append(tag)
                log.warning(f"[reconcile] {tag} 매수 미체결 — 추적 삭제")
                continue
            f = fills.get(pos.order_no.lstrip("0"), {})
            fill_price = f.get("filled_price") or float(kis_pos[sym].get("avg_price", 0) or 0)
            if not f.get("filled_price"):
                result["price_fallback"].append(f"{tag} (매수가=KIS 평균단가)")
            # min: 부분체결이면 held, 수동 보유분이 섞여 있으면 주문수량까지만
            pos.qty, pos.fill_price, pos.status = min(held, pos.qty), fill_price, "open"
            append_slippage({
                "date": today, "strategy": strategy, "symbol": sym,
                "signal_price": round(pos.signal_price, 4),
                "fill_price":   round(fill_price, 4),
                "slippage_pct": round((fill_price - pos.signal_price) / pos.signal_price * 100, 4)
                                if pos.signal_price else 0,
                "qty": pos.qty, "order_no": pos.order_no,
            })
            result["buys_filled"].append(tag)
            continue

        # pending_sell
        sold = pos.qty - held if held < pos.qty else 0
        if sold > 0:
            f = fills.get(pos.sell_order_no.lstrip("0"), {})
            exit_price = f.get("filled_price") or pos.sell_price
            if not f.get("filled_price"):
                result["price_fallback"].append(f"{tag} (매도가=LIMIT가, 실제는 이 이상)")
            pnl = (exit_price - pos.fill_price) * sold
            append_live_trade(strategy, {
                "symbol": sym, "entry_date": pos.entry_date, "exit_date": today,
                "entry_price": round(pos.fill_price, 4), "exit_price": round(exit_price, 4),
                "qty": sold, "pnl": round(pnl, 2),
                "pnl_pct": round((exit_price / pos.fill_price - 1) * 100, 2) if pos.fill_price else 0,
                "order_no": pos.sell_order_no,
            })
        if held <= 0:
            del positions[sym]
            result["sells_filled"].append(tag)
        else:
            # 미체결/부분체결 → 남은 수량으로 open 복귀, sell_attempts 는 유지(재주문가 인하용)
            pos.qty, pos.status = min(held, pos.qty), "open"
            result["sells_unfilled"].append((strategy, sym))
            log.warning(f"[reconcile] {tag} 매도 미체결 (잔고 {held}주) — 재매도 대상")

    for s in strategies:
        save_live_positions(s, all_pos[s])
    return result


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


def backfill_fill_prices(kis: KISClient, apply: bool = False) -> list[dict]:
    """
    reconcile 도입 전 포지션은 fill_price 가 신호가(LIMIT가)로 기록돼 있음 (예: ALLE).
    KIS 평균단가로 교정. 단일 전략 보유 + 잔고 수량 == 로컬 수량일 때만 (추가매수 섞이면 평단 왜곡).
    """
    balance = kis.get_balance()
    if balance.get("_failed"):
        raise RuntimeError("KIS 잔고 조회 실패")
    kis_pos = {p["symbol"]: p for p in balance.get("positions", [])}
    all_pos = {s: load_live_positions(s) for s in STRATEGIES}
    owners: dict[str, int] = {}
    for ps in all_pos.values():
        for sym in ps:
            owners[sym] = owners.get(sym, 0) + 1

    changes = []
    for s, ps in all_pos.items():
        for sym, pos in ps.items():
            kp = kis_pos.get(sym)
            if pos.status != "open" or owners[sym] > 1 or not kp or int(kp["qty"]) != pos.qty:
                continue
            avg = round(float(kp.get("avg_price", 0) or 0), 4)
            if avg > 0 and abs(avg - pos.fill_price) > 0.005:
                changes.append({"strategy": s, "symbol": sym,
                                "old": pos.fill_price, "new": avg})
                pos.fill_price = avg
    if apply:
        for s in STRATEGIES:
            save_live_positions(s, all_pos[s])
    return changes


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
    import sys
    # Windows cp949 콘솔에서 한글 외 기호 출력 시 UnicodeEncodeError 방지
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--sync",   action="store_true", help="KIS 잔고 ↔ 로컬 동기화")
    p.add_argument("--report", action="store_true", help="슬리피지 리포트 출력")
    p.add_argument("--backfill-fill-prices", action="store_true",
                   help="fill_price 를 KIS 평균단가로 교정 (기본: 미리보기)")
    p.add_argument("--apply", action="store_true", help="--backfill-fill-prices 실제 저장")
    args = p.parse_args()

    if args.backfill_fill_prices:
        kis = KISClient.from_config(allow_prod=True)
        changes = backfill_fill_prices(kis, apply=args.apply)
        for c in changes:
            print(f"  {c['strategy']} {c['symbol']}: {c['old']} -> {c['new']}")
        print(f"{len(changes)}건 {'저장됨' if args.apply else '(미리보기 - 저장하려면 --apply)'}")

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
