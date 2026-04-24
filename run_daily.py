"""
Paper Trading 일일 실행 스크립트 (3전략 통합)
장 마감 후 실행: python run_daily.py

전략별 동작:
  [Phase 4]   매일: 진입 신호 생성 + 청산 조건 확인
  [Clenow]    매일: MA100 이탈 청산 / 수요일: 스코어 리밸런싱
  [Weinstein] 매일: MA30 이탈 청산  / 수요일: Stage 2 진입 스캔
"""
import sys
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.indicators.factors import build_factor_matrices
from src.indicators.ichimoku import ichimoku
from src.strategy.factor_stack import generate_factor_signals
from src.notify import telegram
from paper_trading.live_signals import get_clenow_signals, get_weinstein_signals
from paper_trading.simple_tracker import (
    load_simple_positions, save_simple_positions,
    append_simple_trade, SimplePosition,
)
from paper_trading.tracker import (
    load_positions, save_positions,
    load_pending, save_pending,
    append_trade, get_trade_summary,
    PaperPosition, PendingEntry,
)


def check_market_closed(force: bool = False) -> bool:
    """미국 동부시간 기준 장 마감(16:00) 이후인지 확인."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    # 주말
    if now_et.weekday() >= 5:
        return True
    # 장중 (09:30 ~ 16:00)
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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="장 중에도 강제 실행")
    args = parser.parse_args()

    if not check_market_closed(force=args.force):
        sys.exit(0)

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1    = cfg["phase1_breakout_pullback"]
    p2    = cfg["phase2_cloud_support"]
    p3    = cfg["phase3_hybrid"]
    p4    = cfg["phase4_factor_stack"]
    risk  = cfg["risk"]
    tg    = cfg.get("telegram", {})

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
    price_data = fetch_all(tickers, "2015-01-01", None, min_bars=300)
    print(f"  {len(price_data)} 종목 로드")

    end_date = max(df.index[-1] for df in price_data.values())
    today    = end_date  # 마지막 거래일

    regime = compute_regime(
        "2015-01-01", None,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )
    regime_ok   = bool(regime.at[today, "trade_ok"])   if today in regime.index else True
    size_factor = float(regime.at[today, "size_factor"]) if today in regime.index else 1.0

    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p4["momentum_period"],
        bb_period=p4["bbwidth_period"],
    )

    # ── 2. 대기 진입 처리 ─────────────────────────────────────────────────
    positions = load_positions()
    pending   = load_pending()
    messages  = []

    new_entries = []
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

        positions[sym] = PaperPosition(
            symbol=sym,
            entry_date=today.isoformat(),
            entry_price=entry_px,
            stop_initial=entry.stop,
            stop_current=entry.stop,
            targets=[entry_px + w * r for w in
                     [t / entry.r for t in [entry_px + tw * entry.r - entry_px
                                            for tw in [entry.targets[i] - entry.entry_price_est
                                                       for i in range(len(entry.targets))]]]
                     ] if entry.targets else [],
            partial_weights=entry.partial_weights,
            targets_hit=0,
            shares_total=shares,
            shares_remaining=shares,
        )
        # simpler target recalc
        t1_r = (entry.targets[0] - entry.entry_price_est) / entry.r if entry.targets else 3.0
        t_px  = [entry_px + t1_r * r] if entry.targets else [entry_px + 3.0 * r]
        positions[sym].targets = t_px

        msg = (f"📈 <b>진입</b> {sym}\n"
               f"  진입가: ${entry_px:.2f}  스톱: ${entry.stop:.2f}\n"
               f"  R: ${r:.2f}  수량: {shares:.2f}주")
        messages.append(msg)
        new_entries.append(sym)

    # ── 3. 보유 포지션 청산 확인 ──────────────────────────────────────────
    donchian_trail = {
        sym: df["low"].rolling(p1["trail_donchian_period"]).min().shift(1)
        for sym, df in price_data.items()
    }

    to_close = []
    for sym, pos in positions.items():
        if sym not in price_data or today not in price_data[sym].index:
            continue
        bar = price_data[sym].loc[today]

        # 스톱: 갭다운이면 시가 체결, 아니면 스톱가 체결
        if bar["open"] <= pos.stop_current:
            exit_px = bar["open"] * (1 - slippage)
            to_close.append((sym, exit_px, "stop_gap"))
            continue
        if bar["low"] <= pos.stop_current:
            exit_px = pos.stop_current * (1 - slippage)
            to_close.append((sym, exit_px, "stop"))
            continue

        # 목표가: 고가 기준 지정가 체결 (갭업이면 시가 체결)
        if pos.targets_hit < len(pos.targets):
            target_price = pos.targets[pos.targets_hit]
            hit_price    = bar["open"] if bar["open"] >= target_price else (
                           target_price if bar["high"] >= target_price else None)
            if hit_price is not None:
                partial_px     = hit_price * (1 - slippage)
                partial_shares = pos.shares_remaining * pos.partial_weights[pos.targets_hit]
                pos.realized_pnl    += (partial_px - pos.entry_price) * partial_shares
                pos.shares_remaining -= partial_shares
                pos.targets_hit      += 1
                if pos.targets_hit == 1:
                    pos.stop_current = pos.entry_price
                msg = (f"🎯 <b>부분 청산</b> {sym}\n"
                       f"  목표 {pos.targets_hit} 달성 @ ${partial_px:.2f}\n"
                       f"  스톱 → 본전(${pos.entry_price:.2f})")
                messages.append(msg)
                continue

        # 트레일: 저가가 트레일 기준선 이탈 시 청산
        if pos.targets_hit >= len(pos.targets):
            tl = donchian_trail.get(sym)
            if tl is not None and today in tl.index:
                trail_val = tl.at[today]
                if not pd.isna(trail_val) and bar["low"] <= trail_val:
                    exit_px = bar["low"] * (1 - slippage)
                    to_close.append((sym, exit_px, "trail"))

    for sym, exit_px, reason in to_close:
        pos       = positions.pop(sym)
        final_pnl = (exit_px - pos.entry_price) * pos.shares_remaining
        total_pnl = pos.realized_pnl + final_pnl
        init_risk = (pos.entry_price - pos.stop_initial) * pos.shares_total
        r_mult    = total_pnl / init_risk if init_risk > 0 else 0.0

        append_trade({
            "date":       today.isoformat(),
            "symbol":     sym,
            "entry_date": pos.entry_date,
            "entry_price": round(pos.entry_price, 4),
            "exit_price":  round(exit_px, 4),
            "exit_reason": reason,
            "r_multiple":  round(r_mult, 4),
            "pnl":         round(total_pnl, 2),
        })
        icon = "✅" if r_mult > 0 else "❌"
        msg  = (f"{icon} <b>청산</b> {sym} [{reason}]\n"
                f"  진입 ${pos.entry_price:.2f} → 청산 ${exit_px:.2f}\n"
                f"  R: {r_mult:+.2f}R  PnL: ${total_pnl:+.2f}")
        messages.append(msg)

    save_positions(positions)

    # ── 4. 신규 신호 생성 ─────────────────────────────────────────────────
    new_signals = []
    if regime_ok and len(positions) < max_pos:
        for sym, df in price_data.items():
            if sym in positions:
                continue
            sigs = generate_factor_signals(sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom)
            # 오늘 날짜 기준 신호만
            sigs = [s for s in sigs if s.entry_date == today]
            new_signals.extend(sigs)

    new_pending = []
    for sig in new_signals:
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
        msg = (f"🔔 <b>신호</b> {sig.symbol} (내일 시가 진입 예정)\n"
               f"  예상 진입: ${sig.entry_price:.2f}  스톱: ${sig.stop:.2f}\n"
               f"  R: ${sig.r:.2f}  목표: ${sig.targets[0]:.2f}")
        messages.append(msg)

    save_pending(new_pending)

    # ── 5. 현황 요약 ──────────────────────────────────────────────────────
    summary = get_trade_summary()
    status_lines = [
        f"\n📊 <b>Paper Trading 현황</b> [{today.strftime('%Y-%m-%d')}]",
        f"  보유: {len(positions)}종목  |  대기: {len(new_pending)}종목",
        f"  누적 거래: {summary['total_trades']}건  |  승률: {summary.get('win_rate', 0):.1%}",
        f"  Profit Factor: {summary.get('profit_factor', '-')}  |  총 PnL: ${summary.get('total_pnl', 0):+.2f}",
        f"  레짐: {'✅ 정상' if regime_ok else '⚠️ 진입 중단 (SPY/VIX)'}",
    ]
    if positions:
        status_lines.append("\n보유 종목:")
        for sym, pos in positions.items():
            cur = price_data[sym].at[today, "close"] if today in price_data[sym].index else 0
            unreal = (cur - pos.entry_price) * pos.shares_remaining
            status_lines.append(f"  {sym}: 진입 ${pos.entry_price:.2f} | 현재 ${cur:.2f} | 미실현 ${unreal:+.2f}")

    messages.append("\n".join(status_lines))

    # ── 6. Clenow 신호 ────────────────────────────────────────────────────
    is_wednesday = today.weekday() == 2
    cap          = initial_capital
    cl_cfg       = cfg.get("clenow_strategy", {})
    cl_pos       = load_simple_positions("clenow")
    cl_sigs      = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wednesday)
    cl_msgs      = []

    slippage_r = 1 - slippage

    # Clenow 청산
    for sym in cl_sigs["sell_ma100"] + cl_sigs["sell_ranked"]:
        if sym not in cl_pos or sym not in price_data:
            continue
        if today not in price_data[sym].index:
            continue
        exit_px  = price_data[sym].loc[today, "close"] * slippage_r
        pos      = cl_pos.pop(sym)
        pnl      = (exit_px - pos.entry_price) * pos.shares
        reason   = "ma100_exit" if sym in cl_sigs["sell_ma100"] else "rank_exit"
        icon     = "✅" if pnl > 0 else "❌"
        cl_msgs.append(f"{icon} [Clenow 청산] {sym} ({reason}) PnL: ${pnl:+.2f}")
        append_simple_trade("clenow", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 4),
            "shares": pos.shares, "pnl": round(pnl, 2), "reason": reason,
        })
    # Clenow 진입 (수요일)
    n_slots = cl_cfg.get("max_positions", 20)
    alloc   = cap / n_slots
    for sym in cl_sigs["buy"][:n_slots - len(cl_pos)]:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        entry_px = price_data[sym].loc[today, "close"]
        shares   = alloc / entry_px
        cl_pos[sym] = SimplePosition(
            symbol=sym,
            entry_date=today.isoformat(),
            entry_price=entry_px,
            shares=shares,
            strategy="clenow",
        )
        cl_msgs.append(
            f"🔔 [Clenow 매수] {sym} | 예상가 ${entry_px:.2f} | {shares:.1f}주 (${alloc:.0f})")

    save_simple_positions("clenow", cl_pos)

    if cl_msgs:
        messages.append("\n".join(cl_msgs))
    elif is_wednesday:
        messages.append(f"📊 [Clenow] 수요일 스캔 완료 — 변동 없음 (보유 {len(cl_pos)}종목)")

    # ── 7. Weinstein 신호 ─────────────────────────────────────────────────
    w_cfg   = cfg.get("weinstein_strategy", {})
    w_pos   = load_simple_positions("weinstein")
    w_sigs  = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wednesday)
    w_msgs  = []

    # Weinstein 청산
    for sym in w_sigs["sell_ma30"]:
        if sym not in w_pos or sym not in price_data:
            continue
        if today not in price_data[sym].index:
            continue
        exit_px = price_data[sym].loc[today, "close"] * slippage_r
        pos     = w_pos.pop(sym)
        pnl     = (exit_px - pos.entry_price) * pos.shares
        icon    = "✅" if pnl > 0 else "❌"
        w_msgs.append(f"{icon} [Weinstein 청산] {sym} (ma30_exit) PnL: ${pnl:+.2f}")
        append_simple_trade("weinstein", {
            "date": today.isoformat(), "symbol": sym,
            "entry_price": pos.entry_price, "exit_price": round(exit_px, 4),
            "shares": pos.shares, "pnl": round(pnl, 2), "reason": "ma30_exit",
        })
    # Weinstein 진입 (수요일)
    max_w   = w_cfg.get("max_positions", 15)
    alloc_w = cap / max_w
    for sym in w_sigs["buy"][:max_w - len(w_pos)]:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        entry_px = price_data[sym].loc[today, "close"]
        shares   = alloc_w / entry_px
        w_pos[sym] = SimplePosition(
            symbol=sym,
            entry_date=today.isoformat(),
            entry_price=entry_px,
            shares=shares,
            strategy="weinstein",
        )
        w_msgs.append(
            f"🔔 [Weinstein 진입] {sym} | 예상가 ${entry_px:.2f} | {shares:.1f}주 (${alloc_w:.0f})")

    save_simple_positions("weinstein", w_pos)

    if w_msgs:
        messages.append("\n".join(w_msgs))
    elif is_wednesday:
        messages.append(f"📊 [Weinstein] 수요일 스캔 완료 — 변동 없음 (보유 {len(w_pos)}종목)")

    # ── 8. 텔레그램 발송 ──────────────────────────────────────────────────
    full_msg = "\n\n".join(messages) if messages else "\n".join(status_lines)
    telegram.send(full_msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))

    print("\n".join(status_lines).replace("<b>", "").replace("</b>", ""))


if __name__ == "__main__":
    main()
