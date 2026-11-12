"""
Paper Trading 일일 실행 스크립트 (Clenow 단일)
장 마감 후 실행: python run_daily.py [--force] [--reset]

  [Clenow] 매일: MA100 이탈 청산 / 수요일: 스코어 리밸런싱

2026-11-12: Phase 4 · Weinstein 페이퍼 종료. 그때까지의 상태·거래·NAV 는 paper_trading/archive/ 에 보존.
새 후보 전략을 페이퍼에 올릴 때는 이 파일에 섹션을 추가한다.
"""
import csv
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
from src.notify import telegram
from paper_trading.live_signals import get_clenow_signals
from paper_trading.simple_tracker import (
    load_simple_positions, save_simple_positions,
    append_simple_trade, SimplePosition,
    get_simple_trade_summary,
)
from paper_trading.splits import adjust_simple_positions

NAV_FILE           = Path("paper_trading/daily_nav.csv")
NAV_FIELDS         = ["date", "cl_total", "cl_cash", "cl_invested"]
MIN_TODAY_COVERAGE = 0.90   # 당일 봉이 있어야 하는 종목 비율 (미만이면 데이터 장애로 보고 건너뜀)


def _last_nav_date() -> pd.Timestamp | None:
    if not NAV_FILE.exists() or NAV_FILE.stat().st_size == 0:
        return None
    try:
        dates = pd.read_csv(NAV_FILE, usecols=["date"], parse_dates=["date"])["date"]
        return dates.max() if len(dates) else None
    except Exception:
        return None


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
    """Clenow paper trading 상태와 거래 기록을 초기화한다."""
    pos_file    = Path("paper_trading/positions_clenow.json")
    trades_file = Path("paper_trading/trades_clenow.csv")
    pos_file.parent.mkdir(exist_ok=True)
    pos_file.write_text("{}", encoding="utf-8")
    print(f"  초기화: {pos_file}")
    if trades_file.exists():
        trades_file.unlink()
        print(f"  삭제:   {trades_file}")
    print("✅ Paper trading 초기화 완료.")


# 슬롯 금액의 이 비율도 못 채우는 현금이면 그 슬롯은 비워 둔다 (자투리 매수 방지)
MIN_SLOT_FILL = 0.5


def _paper_cash(initial: float, positions: dict, strategy: str) -> float:
    """페이퍼 가용 현금 = 초기자본 − 보유 원가 + 확정손익 (NAV 계산과 같은 식)."""
    cost     = sum(p.entry_price * p.shares for p in positions.values())
    realized = float(get_simple_trade_summary(strategy).get("total_pnl") or 0)
    return initial - cost + realized


def _buy_amount(alloc: float, cash: float) -> float:
    """슬롯 매수 금액 — 현금 한도 내. 슬롯의 MIN_SLOT_FILL 미만이면 0 (매수 안 함)."""
    amt = min(alloc, cash)
    return amt if amt >= alloc * MIN_SLOT_FILL else 0.0


def _get_cur(sym, today, price_data, fallback):
    df = price_data.get(sym)
    if df is not None:
        if today in df.index:
            return df.at[today, "close"]
        # 당일 봉이 빠진 종목(불량 봉 제거)은 직전 종가로 평가 — 진입가로 두면 손익이 0 으로 보인다
        prior = df["close"].loc[:today]
        if len(prior):
            return prior.iloc[-1]
    return fallback


def _cl_stop(sym, today, price_data, ma100_p):
    df = price_data.get(sym)
    if df is None or today not in df.index:
        return None
    ma = df["close"].rolling(ma100_p).mean()
    return float(ma.at[today]) if today in ma.index and pd.notna(ma.at[today]) else None


def _nav(positions: dict, price_data: dict, today, initial: float) -> tuple[float, float, float, float]:
    """(총자산, 예수금, 투자중 시가, 미실현손익)"""
    cost   = sum(p.entry_price * p.shares for p in positions.values())
    unreal = sum((_get_cur(sym, today, price_data, p.entry_price) - p.entry_price) * p.shares
                 for sym, p in positions.items())
    cash   = _paper_cash(initial, positions, "clenow")
    return cash + cost + unreal, cash, cost + unreal, unreal


def build_telegram_message(today, cl_pos, cl_exits, cl_buys, is_wednesday, price_data, cfg) -> str:
    L = []

    def line(s=""): L.append(s)
    def bold(s): return f"<b>{s}</b>"

    ma100_p = cfg.get("clenow_strategy", {}).get("ma100_period", 100)
    initial = float(cfg["backtest"]["initial_capital_usd"])

    line(f"📅 {bold(today.strftime('%Y-%m-%d') + ' Paper Trading')}")
    line()
    line(bold("━━━ 📊 Clenow (모멘텀) ━━━"))

    line(bold(f"보유 {len(cl_pos)}종목"))
    for sym in sorted(cl_pos.keys()):
        pos    = cl_pos[sym]
        cur    = _get_cur(sym, today, price_data, pos.entry_price)
        pct    = (cur / pos.entry_price - 1) * 100
        stop   = _cl_stop(sym, today, price_data, ma100_p)
        stop_s = f" | stop ${stop:.2f}" if stop is not None else ""
        line(f"  {sym}: ${pos.entry_price:.2f}→${cur:.2f} ({pct:+.1f}%){stop_s}")

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

    # ── 요약 ──────────────────────────────────────────────
    total, cash, invested, unreal = _nav(cl_pos, price_data, today, initial)
    s = get_simple_trade_summary("clenow")
    line()
    line(bold("━━━ 📋 요약 ━━━"))
    line(bold(f"총자산: ${total:,.0f} ({(total / initial - 1) * 100:+.2f}%)"))
    line(f"  예수금 ${cash:,.0f} | 투자중 ${invested:,.0f} | 초기자본 ${initial:,.0f}")
    line(f"  미실현 ${unreal:+,.0f}")
    n = s.get("total_trades", 0)
    if n:
        line(f"  확정 {n}건 | WR {s.get('win_rate', 0) * 100:.0f}% | PF {s.get('profit_factor', 0):.2f} "
             f"| ${s.get('total_pnl', 0):+,.0f}")
    else:
        line("  확정 거래 없음")

    return "\n".join(L)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",          action="store_true", help="장 중에도 강제 실행")
    parser.add_argument("--reset",          action="store_true", help="paper trading 상태 전체 초기화")
    parser.add_argument("--refresh",        action="store_true", help="가격 데이터 캐시 무시하고 재다운로드")
    parser.add_argument("--force-wednesday", action="store_true", help="수요일 스캔 강제 실행 (비수요일에도)")
    args = parser.parse_args()

    if args.reset:
        reset_paper_trading()
        return

    if not check_market_closed(force=args.force):
        sys.exit(0)

    cfg  = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    risk = cfg["risk"]
    tg   = cfg.get("telegram", {})

    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage   = risk["slippage_pct"] / 100
    today_str  = date.today().isoformat()

    # ── 1. 데이터 로드 ─────────────────────────────────────────────────────
    print(f"[{today_str}] 데이터 다운로드...")
    tickers    = get_sp500_tickers()
    price_data = fetch_all(tickers, "2015-01-01", None, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} 종목 로드")

    last_dates = pd.Series([df.index[-1] for df in price_data.values()])
    end_date   = last_dates.max()
    today      = end_date

    # 당일 봉 커버리지 — 불량 봉(NaN) 제거 후 당일 데이터가 있는 종목이 적으면 데이터 장애.
    # 그대로 돌리면 순위·청산이 일부 종목만으로 계산된다 → 상태를 건드리지 않고 종료
    coverage = float((last_dates == end_date).mean())
    if coverage < MIN_TODAY_COVERAGE:
        msg = (f"⚠️ <b>페이퍼 실행 건너뜀</b> — {today.date()} 봉이 {coverage:.0%} 종목에만 있음 "
               f"(기준 {MIN_TODAY_COVERAGE:.0%}). yfinance 데이터 장애 추정, 상태 파일 미변경")
        print(msg)
        telegram.send(msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))
        return

    # 같은 봉을 두 번 처리하지 않는다 (수동 재실행·휴장일 — 5/22, 9/04 NAV 중복 기록 이력)
    last_nav = _last_nav_date()
    if last_nav is not None and today <= last_nav:
        print(f"  {today.date()} 봉은 이미 처리됨 (NAV 마지막 {last_nav.date()}) — 종료")
        return

    # ── 2. Clenow 신호 ────────────────────────────────────────────────────
    is_wednesday = today.weekday() == 2 or args.force_wednesday
    cap          = initial_capital
    cl_cfg       = cfg.get("clenow_strategy", {})
    cl_pos       = load_simple_positions("clenow")
    cl_split_msgs = adjust_simple_positions(cl_pos, str(today.date()))
    if cl_split_msgs:
        save_simple_positions("clenow", cl_pos)
        print(f"  [분할 보정] Clenow: {', '.join(cl_split_msgs)}")
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
    # 가용 현금 = 초기자본 − 보유 원가 + 확정손익 (오늘 청산분 포함). 예전엔 현금 확인 없이
    # 슬롯당 고정액을 사서 누적 손실 구간에 현금이 음수가 됐다 (레버리지 = 수익률 과대).
    cl_cash = _paper_cash(cap, cl_pos, "clenow")
    for sym in cl_sigs["buy"][:n_slots - len(cl_pos)]:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        buy_amt = _buy_amount(alloc, cl_cash)
        if buy_amt <= 0:
            print(f"  [Clenow] 현금 부족 (${cl_cash:,.0f} < 슬롯 ${alloc:,.0f} 의 "
                  f"{MIN_SLOT_FILL:.0%}) — {sym} 이후 매수 생략")
            break
        entry_px = price_data[sym].loc[today, "close"]
        shares   = buy_amt / entry_px
        cl_pos[sym] = SimplePosition(
            symbol=sym, entry_date=today.isoformat(),
            entry_price=entry_px, shares=shares, strategy="clenow",
        )
        cl_cash -= buy_amt
        cl_buys.append((sym, entry_px, shares, buy_amt))

    save_simple_positions("clenow", cl_pos)

    # ── 3. 일별 NAV 기록 ─────────────────────────────────────────────────
    cl_t, cl_c, cl_i, _ = _nav(cl_pos, price_data, today, initial_capital)
    NAV_FILE.parent.mkdir(exist_ok=True)
    write_header = not NAV_FILE.exists() or NAV_FILE.stat().st_size == 0
    with NAV_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=NAV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({"date": today.isoformat(), "cl_total": round(cl_t, 2),
                         "cl_cash": round(cl_c, 2), "cl_invested": round(cl_i, 2)})

    # ── 4. 텔레그램 발송 ──────────────────────────────────────────────────
    msg = build_telegram_message(today, cl_pos, cl_exits, cl_buys, is_wednesday, price_data, cfg)
    telegram.send(msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))
    print(msg.replace("<b>", "").replace("</b>", ""))


if __name__ == "__main__":
    main()
