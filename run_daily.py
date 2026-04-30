"""
Paper Trading 일일 실행 스크립트 (3전략 통합)
장 마감 후 실행: python run_daily.py [--force] [--reset]

전략별 동작:
  [Phase 4]   매일: 진입 신호 생성 + 청산 조건 확인
  [Clenow]    매일: MA100 이탈 청산 / 수요일: 스코어 리밸런싱
  [Weinstein] 매일: MA30 이탈 청산  / 수요일: Stage 2 진입 스캔
"""
import sys
from pathlib import Path
from datetime import date, datetime

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.indicators.factors import build_factor_matrices
from src.strategy.factor_stack import generate_factor_signals
from src.notify import telegram
from paper_trading.live_signals import get_clenow_signals, get_weinstein_signals
from paper_trading.simple_tracker import (
    load_simple_positions, save_simple_positions,
    append_simple_trade, SimplePosition,
    get_simple_trade_summary,
)
from paper_trading.tracker import (
    load_positions, save_positions,
    load_pending, save_pending,
    append_trade, get_trade_summary,
    PaperPosition, PendingEntry,
)


def check_market_closed(force: bool = False) -> bool:
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return True
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if market_open <= now_et < market_close:
        if force:
            print(f"⚠️  현재 장 중입니다 (ET {now_et.strftime('%H:%M')}). --force 옵션으로 강제 실행 중...")
            return True
        print(f"⛔ 현재 장 중입니다 (ET {now_et.strftime('%H:%M')}).")
        print("   장 마감 후(ET 16:00 이후) 실행하세요.")
        print("   강제 실행: python run_daily.py --force")
        return False
    return True


def reset_paper_trading() -> None:
    """모든 paper trading 상태와 거래 기록을 초기화한다."""
    state_files = {
        Path("paper_trading/positions.json"):           "{}",
        Path("paper_trading/pending.json"):             "[]",
        Path("paper_trading/positions_clenow.json"):    "{}",
        Path("paper_trading/positions_weinstein.json"): "{}",
    }
    trade_files = [
        Path("paper_trading/trades.csv"),
        Path("paper_trading/trades_clenow.csv"),
        Path("paper_trading/trades_weinstein.csv"),
    ]
    for f, default in state_files.items():
        f.parent.mkdir(exist_ok=True)
        f.write_text(default, encoding="utf-8")
        print(f"  초기화: {f}")
    for f in trade_files:
        if f.exists():
            f.unlink()
            print(f"  삭제:   {f}")
    print("✅ Paper trading 초기화 완료.")


def build_telegram_message(
    today, regime_ok,
    p4_positions, p4_entries, p4_partial_hits, p4_exits, p4_signals,
    cl_pos, cl_exits, cl_buys, is_wednesday,
    w_pos,  w_exits,  w_buys,
    price_data,
) -> str:
    L = []

    def line(s=""): L.append(s)
    def bold(s): return f"<b>{s}</b>"

    line(f"📅 {bold(today.strftime('%Y-%m-%d') + ' Paper Trading')}")
    line(f"레짐: {'✅ 정상' if regime_ok else '⚠️ 진입 중단 (SPY/VIX)'}")

    # ── Phase 4 ───────────────────────────────────────────
    line()
    line(bold("━━━ 📈 Phase 4 (추세추종) ━━━"))

    line(bold(f"보유 {len(p4_positions)}종목"))
    for sym, pos in p4_positions.items():
        cur    = price_data[sym].at[today, "close"] if sym in price_data and today in price_data[sym].index else pos.entry_price
        unreal = (cur - pos.entry_price) * pos.shares_remaining
        pct    = (cur / pos.entry_price - 1) * 100
        s      = "+" if unreal >= 0 else ""
        line(f"  {sym}: ${pos.entry_price:.2f}→${cur:.2f} ({s}{pct:.1f}%) | stop ${pos.stop_current:.2f}")

    if p4_entries:
        line(bold(f"오늘 진입 {len(p4_entries)}건"))
        for sym, entry_px, stop, r in p4_entries:
            line(f"  📈 {sym} ${entry_px:.2f} | stop ${stop:.2f} | R=${r:.2f}")

    if p4_partial_hits:
        line(bold(f"오늘 목표 달성 {len(p4_partial_hits)}건"))
        for sym, target_n, partial_px in p4_partial_hits:
            line(f"  🎯 {sym} 목표{target_n} @ ${partial_px:.2f} → stop 본전 이동")

    line(bold(f"오늘 청산 {len(p4_exits)}건"))
    for sym, exit_px, reason, r_mult, pnl in p4_exits:
        icon = "✅" if pnl >= 0 else "❌"
        line(f"  {icon} {sym} [{reason}] {r_mult:+.2f}R | ${pnl:+.0f}")

    if p4_signals:
        line(bold(f"내일 진입 예정 {len(p4_signals)}건"))
        for sig in p4_signals:
            line(f"  🔔 {sig.symbol} ${sig.entry_price:.2f} | stop ${sig.stop:.2f} | R=${sig.r:.2f}")
    else:
        line("내일 진입 예정 0건")

    # ── Clenow ────────────────────────────────────────────
    line()
    line(bold("━━━ 📊 Clenow (모멘텀) ━━━"))

    line(bold(f"보유 {len(cl_pos)}종목"))
    if cl_pos:
        line(f"  {', '.join(sorted(cl_pos.keys()))}")

    line(bold(f"오늘 청산 {len(cl_exits)}건"))
    for sym, pnl, reason in cl_exits:
        icon = "✅" if pnl >= 0 else "❌"
        line(f"  {icon} {sym} [{reason}] ${pnl:+.0f}")

    if cl_buys:
        line(bold(f"오늘 매수 {len(cl_buys)}건"))
        for sym, entry_px, shares, alloc in cl_buys:
            line(f"  🔔 {sym} ${entry_px:.2f} | {shares:.1f}주 (${alloc:.0f})")
    elif is_wednesday:
        line("오늘 매수 0건 (수요일 스캔 완료)")
    else:
        line("매수 스캔 없음 (수요일에만)")

    # ── Weinstein ─────────────────────────────────────────
    line()
    line(bold("━━━ 🏭 Weinstein (Stage 2) ━━━"))

    line(bold(f"보유 {len(w_pos)}종목"))
    if w_pos:
        line(f"  {', '.join(sorted(w_pos.keys()))}")

    line(bold(f"오늘 청산 {len(w_exits)}건"))
    for sym, pnl in w_exits:
        icon = "✅" if pnl >= 0 else "❌"
        line(f"  {icon} {sym} [ma30_exit] ${pnl:+.0f}")

    if w_buys:
        line(bold(f"오늘 진입 {len(w_buys)}건"))
        for sym, entry_px, shares, alloc_w in w_buys:
            line(f"  🔔 {sym} ${entry_px:.2f} | {shares:.1f}주 (${alloc_w:.0f})")
    elif is_wednesday:
        line("오늘 진입 0건 (수요일 스캔 완료)")
    else:
        line("진입 스캔 없음 (수요일에만)")

    # ── 전략 요약 ─────────────────────────────────────────
    line()
    line(bold("━━━ 📋 전략 요약 ━━━"))

    p4_sum = get_trade_summary()
    cl_sum = get_simple_trade_summary("clenow")
    w_sum  = get_simple_trade_summary("weinstein")

    def fmt_row(name: str, s: dict) -> str:
        n = s.get("total_trades", 0)
        if n == 0:
            return f"  {name:<12} 거래 없음"
        wr  = s.get("win_rate", 0) * 100
        pf  = s.get("profit_factor", 0)
        pnl = s.get("total_pnl", 0)
        return f"  {name:<12} {n}건 | WR {wr:.0f}% | PF {pf:.2f} | ${pnl:+.0f}"

    line(fmt_row("Phase 4",   p4_sum))
    line(fmt_row("Clenow",    cl_sum))
    line(fmt_row("Weinstein", w_sum))

    return "\n".join(L)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",   action="store_true", help="장 중에도 강제 실행")
    parser.add_argument("--reset",   action="store_true", help="paper trading 상태 전체 초기화")
    parser.add_argument("--refresh", action="store_true", help="가격 데이터 캐시 무시하고 재다운로드")
    args = parser.parse_args()

    if args.reset:
        reset_paper_trading()
        return

    if not check_market_closed(force=args.force):
        sys.exit(0)

    cfg  = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1   = cfg["phase1_breakout_pullback"]
    p2   = cfg["phase2_cloud_support"]
    p3   = cfg["phase3_hybrid"]
    p4   = cfg["phase4_factor_stack"]
    risk = cfg["risk"]
    tg   = cfg.get("telegram", {})

    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]

    initial_capital = cfg["backtest"]["initial_capital_usd"]
    risk_pct   = risk["risk_per_trade_pct"] / 100
    max_pos    = risk["max_positions"]
    slippage   = risk["slippage_pct"] / 100
    today_str  = date.today().isoformat()

    # ── 1. 데이터 로드 ─────────────────────────────────────────────────────
    print(f"[{today_str}] 데이터 다운로드...")
    tickers    = get_sp500_tickers()
    price_data = fetch_all(tickers, "2015-01-01", None, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} 종목 로드")

    end_date = max(df.index[-1] for df in price_data.values())
    today    = end_date

    regime = compute_regime(
        "2015-01-01", None,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )
    regime_ok   = bool(regime.at[today, "trade_ok"])     if today in regime.index else True
    size_factor = float(regime.at[today, "size_factor"]) if today in regime.index else 1.0

    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p4["momentum_period"],
        bb_period=p4["bbwidth_period"],
    )

    # ── 2. 대기 진입 처리 ─────────────────────────────────────────────────
    positions  = load_positions()
    pending    = load_pending()
    p4_entries = []  # (sym, entry_px, stop, r)

    for entry in pending:
        sym = entry.symbol
        if sym in positions or sym not in price_data:
            continue
        df_sym = price_data[sym]
        if today not in df_sym.index:
            continue

        entry_px = df_sym.at[today, "open"] * (1 + slippage)
        r        = entry_px - entry.stop
        if r <= 0:
            continue

        risk_amt = initial_capital * risk_pct * size_factor
        shares   = risk_amt / r
        t1_r     = (entry.targets[0] - entry.entry_price_est) / entry.r if entry.targets else 3.0

        positions[sym] = PaperPosition(
            symbol=sym,
            entry_date=today.isoformat(),
            entry_price=entry_px,
            stop_initial=entry.stop,
            stop_current=entry.stop,
            targets=[entry_px + t1_r * r],
            partial_weights=entry.partial_weights,
            targets_hit=0,
            shares_total=shares,
            shares_remaining=shares,
        )
        p4_entries.append((sym, entry_px, entry.stop, r))

    # ── 3. 보유 포지션 청산 확인 ──────────────────────────────────────────
    donchian_trail = {
        sym: df["low"].rolling(p1["trail_donchian_period"]).min().shift(1)
        for sym, df in price_data.items()
    }

    to_close        = []
    p4_partial_hits = []  # (sym, target_n, partial_px)

    for sym, pos in positions.items():
        if sym not in price_data or today not in price_data[sym].index:
            continue
        bar = price_data[sym].loc[today]

        if bar["open"] <= pos.stop_current:
            to_close.append((sym, bar["open"] * (1 - slippage), "stop_gap"))
            continue
        if bar["low"] <= pos.stop_current:
            to_close.append((sym, pos.stop_current * (1 - slippage), "stop"))
            continue

        if pos.targets_hit < len(pos.targets):
            target_price = pos.targets[pos.targets_hit]
            hit_price    = bar["open"] if bar["open"] >= target_price else (
                           target_price if bar["high"] >= target_price else None)
            if hit_price is not None:
                partial_px           = hit_price * (1 - slippage)
                partial_shares       = pos.shares_remaining * pos.partial_weights[pos.targets_hit]
                pos.realized_pnl    += (partial_px - pos.entry_price) * partial_shares
                pos.shares_remaining -= partial_shares
                pos.targets_hit      += 1
                if pos.targets_hit == 1:
                    pos.stop_current = pos.entry_price
                p4_partial_hits.append((sym, pos.targets_hit, partial_px))
                continue

        if pos.targets_hit >= len(pos.targets):
            tl = donchian_trail.get(sym)
            if tl is not None and today in tl.index:
                trail_val = tl.at[today]
                if not pd.isna(trail_val) and bar["low"] <= trail_val:
                    to_close.append((sym, bar["low"] * (1 - slippage), "trail"))

    p4_exits = []  # (sym, exit_px, reason, r_mult, pnl)
    for sym, exit_px, reason in to_close:
        pos       = positions.pop(sym)
        final_pnl = (exit_px - pos.entry_price) * pos.shares_remaining
        total_pnl = pos.realized_pnl + final_pnl
        init_risk = (pos.entry_price - pos.stop_initial) * pos.shares_total
        r_mult    = total_pnl / init_risk if init_risk > 0 else 0.0
        append_trade({
            "date":        today.isoformat(),
            "symbol":      sym,
            "entry_date":  pos.entry_date,
            "entry_price": round(pos.entry_price, 4),
            "exit_price":  round(exit_px, 4),
            "exit_reason": reason,
            "r_multiple":  round(r_mult, 4),
            "pnl":         round(total_pnl, 2),
        })
        p4_exits.append((sym, exit_px, reason, r_mult, total_pnl))

    save_positions(positions)

    # ── 4. 신규 신호 생성 ─────────────────────────────────────────────────
    p4_signals  = []
    new_pending = []
    if regime_ok and len(positions) < max_pos:
        raw_sigs = []
        for sym, df in price_data.items():
            if sym in positions:
                continue
            sigs = generate_factor_signals(sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom)
            raw_sigs.extend(s for s in sigs if s.entry_date == today)

        for sig in raw_sigs:
            if len(positions) + len(new_pending) >= max_pos:
                break
            new_pending.append(PendingEntry(
                symbol=sig.symbol,
                signal_date=today.isoformat(),
                entry_price_est=sig.entry_price,
                stop=sig.stop,
                r=sig.r,
                targets=sig.targets,
                partial_weights=sig.partial_weights,
            ))
            p4_signals.append(sig)

    save_pending(new_pending)

    # ── 5. Clenow 신호 ────────────────────────────────────────────────────
    is_wednesday = today.weekday() == 2
    cap          = initial_capital
    cl_cfg       = cfg.get("clenow_strategy", {})
    cl_pos       = load_simple_positions("clenow")
    cl_sigs      = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wednesday)
    cl_exits     = []  # (sym, pnl, reason)
    cl_buys      = []  # (sym, entry_px, shares, alloc)
    slippage_r   = 1 - slippage

    for sym in cl_sigs["sell_ma100"] + cl_sigs["sell_ranked"]:
        if sym not in cl_pos or sym not in price_data:
            continue
        if today not in price_data[sym].index:
            continue
        exit_px = price_data[sym].loc[today, "close"] * slippage_r
        pos     = cl_pos.pop(sym)
        pnl     = (exit_px - pos.entry_price) * pos.shares
        reason  = "ma100_exit" if sym in cl_sigs["sell_ma100"] else "rank_exit"
        cl_exits.append((sym, pnl, reason))
        append_simple_trade("clenow", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 4),
            "shares": pos.shares, "pnl": round(pnl, 2), "reason": reason,
        })

    n_slots = cl_cfg.get("max_positions", 20)
    alloc   = cap / n_slots
    for sym in cl_sigs["buy"][:n_slots - len(cl_pos)]:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        entry_px = price_data[sym].loc[today, "close"]
        shares   = alloc / entry_px
        cl_pos[sym] = SimplePosition(
            symbol=sym, entry_date=today.isoformat(),
            entry_price=entry_px, shares=shares, strategy="clenow",
        )
        cl_buys.append((sym, entry_px, shares, alloc))

    save_simple_positions("clenow", cl_pos)

    # ── 6. Weinstein 신호 ─────────────────────────────────────────────────
    w_cfg   = cfg.get("weinstein_strategy", {})
    w_pos   = load_simple_positions("weinstein")
    w_sigs  = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wednesday)
    w_exits = []  # (sym, pnl)
    w_buys  = []  # (sym, entry_px, shares, alloc_w)

    for sym in w_sigs["sell_ma30"]:
        if sym not in w_pos or sym not in price_data:
            continue
        if today not in price_data[sym].index:
            continue
        exit_px = price_data[sym].loc[today, "close"] * slippage_r
        pos     = w_pos.pop(sym)
        pnl     = (exit_px - pos.entry_price) * pos.shares
        w_exits.append((sym, pnl))
        append_simple_trade("weinstein", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 4),
            "shares": pos.shares, "pnl": round(pnl, 2), "reason": "ma30_exit",
        })

    max_w   = w_cfg.get("max_positions", 15)
    alloc_w = cap / max_w
    for sym in w_sigs["buy"][:max_w - len(w_pos)]:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        entry_px = price_data[sym].loc[today, "close"]
        shares   = alloc_w / entry_px
        w_pos[sym] = SimplePosition(
            symbol=sym, entry_date=today.isoformat(),
            entry_price=entry_px, shares=shares, strategy="weinstein",
        )
        w_buys.append((sym, entry_px, shares, alloc_w))

    save_simple_positions("weinstein", w_pos)

    # ── 7. 텔레그램 발송 ──────────────────────────────────────────────────
    msg = build_telegram_message(
        today, regime_ok,
        positions, p4_entries, p4_partial_hits, p4_exits, p4_signals,
        cl_pos, cl_exits, cl_buys, is_wednesday,
        w_pos,  w_exits,  w_buys,
        price_data,
    )
    telegram.send(msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))
    print(msg.replace("<b>", "").replace("</b>", ""))


if __name__ == "__main__":
    main()
