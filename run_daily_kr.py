"""
Paper Trading 일일 실행 — KR (KOSPI200, Clenow + Weinstein)

장 마감 후 실행 (KST 16:00 이후):
    python run_daily_kr.py
    python run_daily_kr.py --force         # 장 중 강제 실행 (테스트)
    python run_daily_kr.py --refresh       # 캐시 무시 재다운로드
    python run_daily_kr.py --print-only    # 텔레그램 전송 없이 콘솔만
    python run_daily_kr.py --reset         # KR 포지션·거래 초기화

Phase 4 는 미국 전용이라 KR 트랙에는 없다. Clenow + Weinstein 만 운영.
NAV 추적: paper_trading/daily_nav_kr.csv (KRW).
"""
from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from src.fetch.universe import get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.markets import get_profile
from paper_trading.live_signals import get_clenow_signals, get_weinstein_signals
from paper_trading.simple_tracker import (
    load_simple_positions, save_simple_positions,
    append_simple_trade, get_simple_trade_summary,
    SimplePosition,
)
from src.notify import telegram


NAV_CSV = Path("paper_trading/daily_nav_kr.csv")
MARKET  = "kr"


def check_kospi_closed(force: bool = False) -> bool:
    """KOSPI 정규장 09:00~15:30 KST. 주말/장중이면 False."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    if now.weekday() >= 5:
        return True   # 주말은 last trading day 처리 가능
    open_t  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if open_t <= now < close_t:
        if force:
            print(f"[WARN] 장 중 실행 (KST {now:%H:%M}) — force")
            return True
        print(f"[STOP] 장 중 실행 차단 (KST {now:%H:%M}). 15:30 이후 또는 --force.")
        return False
    return True


def reset_state() -> None:
    for f in [
        "paper_trading/positions_clenow_kr.json",
        "paper_trading/positions_weinstein_kr.json",
    ]:
        Path(f).write_text("{}", encoding="utf-8")
    for f in [
        "paper_trading/trades_clenow_kr.csv",
        "paper_trading/trades_weinstein_kr.csv",
    ]:
        Path(f).unlink(missing_ok=True)
    if NAV_CSV.exists():
        NAV_CSV.unlink()
    print("[reset] KR 페이퍼 상태 초기화 완료")


def _get_cur(sym, today, price_data, fallback) -> float:
    df = price_data.get(sym)
    if df is None or today not in df.index:
        return fallback
    return float(df.loc[today, "close"])


def _cl_stop(sym, today, price_data, ma100_p) -> float | None:
    df = price_data.get(sym)
    if df is None:
        return None
    ma = df["close"].rolling(ma100_p).mean()
    val = ma.reindex([today], method="ffill")
    return float(val.iloc[0]) if not val.empty and pd.notna(val.iloc[0]) else None


def _w_stop(sym, today, price_data, ma30_p, weekly_freq) -> float | None:
    from src.strategy.weinstein_stage2 import _resample_weekly
    df = price_data.get(sym)
    if df is None:
        return None
    wdf = _resample_weekly(df, freq=weekly_freq)
    ma  = wdf["close"].rolling(ma30_p).mean()
    val = ma.reindex([today], method="ffill")
    return float(val.iloc[0]) if not val.empty and pd.notna(val.iloc[0]) else None


def append_nav(date, cl_total, cl_cash, cl_invested, w_total, w_cash, w_invested) -> None:
    NAV_CSV.parent.mkdir(exist_ok=True)
    header = not NAV_CSV.exists()
    combined  = cl_total + w_total
    combo6040 = 0.6 * cl_total + 0.4 * w_total
    with NAV_CSV.open("a", encoding="utf-8") as fh:
        if header:
            fh.write("date,cl_total,cl_cash,cl_invested,w_total,w_cash,w_invested,combined,combo_6040\n")
        fh.write(
            f"{date.isoformat()},{cl_total:.0f},{cl_cash:.0f},{cl_invested:.0f},"
            f"{w_total:.0f},{w_cash:.0f},{w_invested:.0f},{combined:.0f},{combo6040:.0f}\n"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",      action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--reset",      action="store_true")
    parser.add_argument("--force-wednesday", action="store_true")
    args = parser.parse_args()

    if args.reset:
        reset_state()
        return

    if not check_kospi_closed(args.force):
        return

    profile = get_profile(MARKET)
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {
        "code":         profile.code,
        "regime_index": profile.index_ticker,
        "currency":     profile.currency,
    }
    cfg.setdefault("clenow_strategy", {}).setdefault("min_price",   profile.min_price)
    cfg["clenow_strategy"].setdefault("index_ticker", profile.index_ticker)
    cfg.setdefault("weinstein_strategy", {}).setdefault("min_price",   profile.min_price)
    cfg["weinstein_strategy"].setdefault("weekly_freq", profile.calendar_freq)

    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    initial_capital = float(cfg["backtest"]["initial_capital_usd"])  # KR 도 동일 단위로 출발

    # ── 데이터 로드 ──────────────────────────────────────────────────────
    print(f"[KR] KOSPI200 데이터 로드 중...")
    tickers = get_kospi200_tickers()
    if profile.index_ticker not in tickers:
        tickers = [profile.index_ticker] + tickers
    price_data = fetch_all(tickers, start, end, min_bars=150,
                           refresh=args.refresh, market=MARKET)
    print(f"  {len(price_data)} 종목 로드 완료")

    if profile.index_ticker not in price_data:
        print(f"[ERROR] {profile.index_ticker} 데이터 없음")
        return

    # 최신 거래일
    today = max(df.index.max() for df in price_data.values() if not df.empty)
    is_wednesday = today.weekday() == 2 or args.force_wednesday

    # ── 레짐 ─────────────────────────────────────────────────────────────
    regime = compute_regime(start, end, market=MARKET)
    regime_ok = bool(regime.loc[today, "trade_ok"]) if today in regime.index else True

    slippage_r = 1 - cfg["risk"]["slippage_pct"] / 100

    # ── Clenow ───────────────────────────────────────────────────────────
    cl_cfg = cfg.get("clenow_strategy", {})
    cl_pos = load_simple_positions("clenow", market=MARKET)
    cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wednesday)
    cl_exits = []
    cl_buys  = []

    for sym in cl_sigs["sell_ma100"] + cl_sigs["sell_ranked"]:
        if sym not in cl_pos or sym not in price_data or today not in price_data[sym].index:
            continue
        exit_px = price_data[sym].loc[today, "close"] * slippage_r
        pos = cl_pos.pop(sym)
        pnl = (exit_px - pos.entry_price) * pos.shares
        reason = "ma100_exit" if sym in cl_sigs["sell_ma100"] else "rank_exit"
        cl_exits.append((sym, pnl, reason))
        append_simple_trade("clenow", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 2),
            "shares": pos.shares, "pnl": round(pnl, 0), "reason": reason,
        }, market=MARKET)

    if regime_ok:
        n_slots = cl_cfg.get("max_positions", 5)
        alloc   = initial_capital / n_slots
        for sym in cl_sigs["buy"][:n_slots - len(cl_pos)]:
            if sym not in price_data or today not in price_data[sym].index:
                continue
            entry_px = float(price_data[sym].loc[today, "close"])
            if entry_px <= 0:
                continue
            shares = alloc / entry_px
            cl_pos[sym] = SimplePosition(symbol=sym, entry_date=today.isoformat(),
                                         entry_price=entry_px, shares=shares,
                                         strategy="clenow")
            cl_buys.append((sym, entry_px, shares, alloc))

    save_simple_positions("clenow", cl_pos, market=MARKET)

    # ── Weinstein ────────────────────────────────────────────────────────
    w_cfg = cfg.get("weinstein_strategy", {})
    w_pos = load_simple_positions("weinstein", market=MARKET)
    w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wednesday)
    w_exits = []
    w_buys  = []

    for sym in w_sigs["sell_ma30"]:
        if sym not in w_pos or sym not in price_data or today not in price_data[sym].index:
            continue
        exit_px = price_data[sym].loc[today, "close"] * slippage_r
        pos = w_pos.pop(sym)
        pnl = (exit_px - pos.entry_price) * pos.shares
        w_exits.append((sym, pnl))
        append_simple_trade("weinstein", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 2),
            "shares": pos.shares, "pnl": round(pnl, 0), "reason": "ma30_exit",
        }, market=MARKET)

    if regime_ok:
        n_w   = w_cfg.get("max_positions", 4)
        alloc = initial_capital / n_w
        for sym in w_sigs["buy"][:n_w - len(w_pos)]:
            if sym not in price_data or today not in price_data[sym].index:
                continue
            entry_px = float(price_data[sym].loc[today, "close"])
            if entry_px <= 0:
                continue
            shares = alloc / entry_px
            w_pos[sym] = SimplePosition(symbol=sym, entry_date=today.isoformat(),
                                        entry_price=entry_px, shares=shares,
                                        strategy="weinstein")
            w_buys.append((sym, entry_px, shares, alloc))

    save_simple_positions("weinstein", w_pos, market=MARKET)

    # ── 메시지 + NAV ──────────────────────────────────────────────────────
    sym_c = profile.currency_symbol

    L = [f"📅 {today:%Y-%m-%d} KR Paper (KOSPI200)"]
    L.append(f"레짐 (KOSPI MA50): {'OK' if regime_ok else '진입 중단'}")
    L.append("")
    L.append("━━━ Clenow ━━━")
    L.append(f"보유 {len(cl_pos)}종목")
    cl_unreal = 0.0
    for sym, pos in sorted(cl_pos.items()):
        cur = _get_cur(sym, today, price_data, pos.entry_price)
        unreal = (cur - pos.entry_price) * pos.shares
        cl_unreal += unreal
        pct = (cur / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
        stop = _cl_stop(sym, today, price_data, cl_cfg.get("ma100_period", 100))
        sstr = f" stop {sym_c}{stop:,.0f}" if stop else ""
        L.append(f"  {sym}: {sym_c}{pos.entry_price:,.0f}→{sym_c}{cur:,.0f} ({pct:+.1f}%){sstr}")
    if cl_exits:
        L.append(f"청산 {len(cl_exits)}건")
        for sym, pnl, r in cl_exits:
            ic = "✅" if pnl >= 0 else "❌"
            L.append(f"  {ic} {sym} [{r}] {sym_c}{pnl:+,.0f}")
    if cl_buys:
        L.append(f"매수 {len(cl_buys)}건")
        for sym, px, sh, a in cl_buys:
            L.append(f"  🔔 {sym} {sym_c}{px:,.0f} × {sh:.2f}주")
    elif is_wednesday:
        L.append("매수 0건 (수요일 스캔 완료)")

    L.append("")
    L.append("━━━ Weinstein ━━━")
    L.append(f"보유 {len(w_pos)}종목")
    w_unreal = 0.0
    for sym, pos in sorted(w_pos.items()):
        cur = _get_cur(sym, today, price_data, pos.entry_price)
        unreal = (cur - pos.entry_price) * pos.shares
        w_unreal += unreal
        pct = (cur / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
        stop = _w_stop(sym, today, price_data, w_cfg.get("ma30_period", 30),
                       cfg["weinstein_strategy"].get("weekly_freq", "W-FRI"))
        sstr = f" stop {sym_c}{stop:,.0f}" if stop else ""
        L.append(f"  {sym}: {sym_c}{pos.entry_price:,.0f}→{sym_c}{cur:,.0f} ({pct:+.1f}%){sstr}")
    if w_exits:
        L.append(f"청산 {len(w_exits)}건")
        for sym, pnl in w_exits:
            ic = "✅" if pnl >= 0 else "❌"
            L.append(f"  {ic} {sym} [ma30_exit] {sym_c}{pnl:+,.0f}")
    if w_buys:
        L.append(f"매수 {len(w_buys)}건")
        for sym, px, sh, a in w_buys:
            L.append(f"  🔔 {sym} {sym_c}{px:,.0f} × {sh:.2f}주")
    elif is_wednesday:
        L.append("매수 0건 (수요일 스캔 완료)")

    # NAV 추적
    cl_cost = sum(p.entry_price * p.shares for p in cl_pos.values())
    w_cost  = sum(p.entry_price * p.shares for p in w_pos.values())
    cl_real = float(get_simple_trade_summary("clenow",    market=MARKET)["total_pnl"])
    w_real  = float(get_simple_trade_summary("weinstein", market=MARKET)["total_pnl"])

    cl_invested = cl_cost + cl_unreal
    w_invested  = w_cost  + w_unreal
    cl_cash = initial_capital - cl_cost + cl_real
    w_cash  = initial_capital - w_cost  + w_real
    cl_total = cl_cash + cl_invested
    w_total  = w_cash  + w_invested

    L.append("")
    L.append("━━━ NAV ━━━")
    L.append(f"Clenow:    {sym_c}{cl_total:,.0f}  (현금 {sym_c}{cl_cash:,.0f}, 평가 {sym_c}{cl_invested:,.0f})")
    L.append(f"Weinstein: {sym_c}{w_total:,.0f}  (현금 {sym_c}{w_cash:,.0f}, 평가 {sym_c}{w_invested:,.0f})")
    combo6040 = 0.6 * cl_total + 0.4 * w_total
    L.append(f"60:40:     {sym_c}{combo6040:,.0f}")

    append_nav(today, cl_total, cl_cash, cl_invested, w_total, w_cash, w_invested)

    msg = "\n".join(L)
    # cp949 콘솔 호환: emoji 출력 안전 처리
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))

    if not args.print_only:
        try:
            tg = cfg.get("telegram", {}) or cfg.get("notify", {}) or {}
            token   = tg.get("bot_token", "") or tg.get("telegram_token", "")
            chat_id = tg.get("chat_id", "")   or tg.get("telegram_chat_id", "")
            if token and chat_id:
                telegram.send(msg, token=token, chat_id=chat_id)
        except Exception as e:
            print(f"[telegram] 전송 실패: {e}")


if __name__ == "__main__":
    main()
