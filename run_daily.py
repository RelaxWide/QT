"""
Paper Trading 일일 실행 스크립트 (Clenow 단일)
장 마감 후 실행: python run_daily.py [--force] [--reset]

  [Clenow] 매일: MA100 이탈 청산 / 수요일: 스코어 리밸런싱
           (SPY > MA200 일 때만 매수, 급등락 종목 제외, 업종당 종목 수 제한, 변동성 역가중 — 2026-11-12 원래 설계 복원)

마지막 NAV 이후 아직 처리 안 한 거래일을 날짜순으로 전부 처리한다 (하루 여러 번 실행해도 안전).
예전엔 최신 봉 하나만 처리해서, 실행 시점에 yfinance 당일 봉이 없으면 그날이 영영 빠졌다
(2026-11 첫 주 8세션 중 4일 누락 — 수요일 리밸런싱도 함께 빠짐).

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

from src.fetch.universe import get_sp500_tickers, get_sp500_sectors
from src.fetch.prices import fetch_all, fetch_prices
from src.strategy.clenow_momentum import spy_regime_ok
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
PAPER_MAX_PER_SECTOR = 5    # 20종목 기준 업종당 최대 (실계좌는 8종목에 2)
MIN_TODAY_COVERAGE = 0.90   # 당일 봉이 있어야 하는 종목 비율 (미만이면 아직 데이터가 덜 온 날로 보고 미룸)
STALE_ALERT_BDAYS  = 3      # NAV 가 이만큼 영업일 밀리면 텔레그램 경고 (그 전엔 다음 실행이 따라잡는다)


def _pending_days(price_data: dict, last_nav) -> list[tuple[pd.Timestamp, float]]:
    """마지막 NAV 이후의 거래일과 그날 봉이 있는 종목 비율. 첫 실행(NAV 없음)이면 최신 봉 하루만."""
    counts: dict = {}
    for df in price_data.values():
        idx = df.index[df.index > last_nav] if last_nav is not None else df.index[-1:]
        for d in idx:
            counts[d] = counts.get(d, 0) + 1
    n = max(len(price_data), 1)
    return sorted((d, c / n) for d, c in counts.items())


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
    # 지수에서 빠진 보유 종목도 가격을 받아야 청산·평가가 된다
    held_extra = [s for s in load_simple_positions("clenow") if s not in set(tickers)]
    if held_extra:
        print(f"  유니버스 밖 보유 종목 가격 추가 수집: {held_extra}")
        tickers = tickers + held_extra
    price_data = fetch_all(tickers, "2015-01-01", None, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} 종목 로드")

    # 처리할 날 = 마지막 NAV 이후 거래일 중 봉이 충분히 온 날 (같은 봉을 두 번 처리하지 않는다).
    # 봉이 덜 온 날(불량 봉 NaN 제거 후 커버리지 미달)은 건드리지 않고 다음 실행으로 미룬다 —
    # 그대로 돌리면 순위·청산이 일부 종목만으로 계산된다.
    last_nav = _last_nav_date()
    pending  = _pending_days(price_data, last_nav)
    # 날짜순으로 처리하다가 **첫 미충족 날짜에서 멈춘다.** 건너뛰고 뒷날을 처리하면 NAV 마지막 날짜가
    # 넘어가 버려 그날은 다시는 처리 대상이 되지 않는다 (11/10 80%·11/11 100% 로 재현).
    # 미충족 상태가 오래가면 아래 STALE_ALERT_BDAYS 경고로 드러난다.
    days = []
    for d, cov in pending:
        if cov < MIN_TODAY_COVERAGE:
            print(f"  {d.date()} 봉이 {cov:.0%} 종목에만 있음 (기준 {MIN_TODAY_COVERAGE:.0%}) — "
                  f"여기서 멈춤 (이후 날짜도 다음 실행으로 미룸)")
            break
        days.append(d)
    if not days:
        print(f"  처리할 새 봉 없음 (NAV 마지막 {last_nav.date() if last_nav is not None else '-'}) — 종료")
        if last_nav is not None:
            behind = len(pd.bdate_range(last_nav, pd.Timestamp.today().normalize())) - 1
            if behind >= STALE_ALERT_BDAYS:
                msg = (f"⚠️ <b>페이퍼 NAV 정체</b> — 마지막 {last_nav.date()} ({behind}영업일 전). "
                       f"yfinance 데이터 장애 추정, 상태 파일 미변경")
                telegram.send(msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))
        return

    # 레짐(SPY > MA200)·업종 정보 — 못 받으면 매수는 보류/업종 제한 없이 진행
    try:
        # 항상 새로 받는다 — 캐시(30영업일 유효)의 낡은 SPY 로 레짐을 판정하지 않도록
        spy_close = fetch_prices("SPY", "2015-01-01", None, refresh=True)["close"]
    except Exception as e:
        print(f"  SPY 가격 조회 실패 — 이번 실행은 신규 매수 보류: {e}")
        spy_close = pd.Series(dtype=float)
    sectors = get_sp500_sectors()

    data_end = str(max(df.index[-1] for df in price_data.values()).date())   # 수정주가 기준일

    cl_exits_all, cl_buys_all = [], []
    tag = len(days) > 1
    for today in days:
        is_wed = today.weekday() == 2 or (args.force_wednesday and today == days[-1])
        exits, buys, cl_pos = _process_day(today, is_wed, price_data, cfg, initial_capital, slippage,
                                           spy_close, sectors, data_end)
        d = f" {today:%m/%d}"
        cl_exits_all += [(s, pnl, reason + d) if tag else (s, pnl, reason) for s, pnl, reason in exits]
        cl_buys_all  += buys
        print(f"  {today.date()} 처리: 청산 {len(exits)} / 매수 {len(buys)}")

    # ── 3. 텔레그램 발송 ──────────────────────────────────────────────────
    today = days[-1]
    is_wednesday = any(d.weekday() == 2 for d in days) or args.force_wednesday
    msg = build_telegram_message(today, cl_pos, cl_exits_all, cl_buys_all, is_wednesday, price_data, cfg)
    if is_wednesday and spy_regime_ok(spy_close, today) is not True:
        msg += "\n⏸️ 레짐 OFF (SPY 200일선 아래 또는 판정 불가) — 신규 매수 보류"
    if tag:
        msg += "\n\n⏪ 밀린 날 함께 처리: " + ", ".join(f"{d:%m/%d}" for d in days)
    telegram.send(msg, token=tg.get("bot_token", ""), chat_id=tg.get("chat_id", ""))
    print(msg.replace("<b>", "").replace("</b>", ""))


def _process_day(today, is_wednesday, price_data, cfg, initial_capital, slippage, spy_close, sectors,
                 data_end: str):
    """하루치 Clenow 처리: 청산 → (수요일) 매수 → 포지션 저장 → NAV 한 줄. (exits, buys, positions) 반환.
    data_end: 가격 데이터의 마지막 봉 날짜 — 수정주가의 기준일 (분할 보정 마커에 쓴다)."""
    cap          = initial_capital
    cl_cfg       = cfg.get("clenow_strategy", {})
    cl_pos       = load_simple_positions("clenow")
    cl_split_msgs = adjust_simple_positions(cl_pos, str(today.date()), as_of=data_end)
    if cl_split_msgs:
        save_simple_positions("clenow", cl_pos)
        print(f"  [분할 보정] Clenow: {', '.join(cl_split_msgs)}")
    regime_ok    = spy_regime_ok(spy_close, today, cl_cfg.get("spy_ma200_period", 200)) is True
    cl_sigs      = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wednesday,
                                      regime_ok=regime_ok, sectors=sectors,
                                      max_per_sector=PAPER_MAX_PER_SECTOR)
    size_mult    = cl_sigs.get("size_mult", {})
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
        buy_amt = _buy_amount(alloc * size_mult.get(sym, 1.0), cl_cash)   # 변동성 역가중
        if buy_amt <= 0:
            print(f"  [Clenow] 현금 부족 (${cl_cash:,.0f} < 슬롯 ${alloc:,.0f} 의 "
                  f"{MIN_SLOT_FILL:.0%}) — {sym} 이후 매수 생략")
            break
        entry_px = price_data[sym].loc[today, "close"]
        shares   = buy_amt / entry_px
        cl_pos[sym] = SimplePosition(
            symbol=sym, entry_date=today.isoformat(),
            entry_price=entry_px, shares=shares, strategy="clenow",
            # 진입가는 이미 "데이터 마지막 날까지의 분할이 반영된" 수정주가다 → 과거 날짜를 따라잡으며
            # 산 포지션에 그 뒤 날짜의 분할을 다시 적용하지 않도록 마커를 데이터 기준일로 둔다
            split_adjusted_through=data_end,
        )
        cl_cash -= buy_amt
        cl_buys.append((sym, entry_px, shares, buy_amt))

    save_simple_positions("clenow", cl_pos)

    # 일별 NAV 기록
    cl_t, cl_c, cl_i, _ = _nav(cl_pos, price_data, today, initial_capital)
    NAV_FILE.parent.mkdir(exist_ok=True)
    write_header = not NAV_FILE.exists() or NAV_FILE.stat().st_size == 0
    with NAV_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=NAV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({"date": today.isoformat(), "cl_total": round(cl_t, 2),
                         "cl_cash": round(cl_c, 2), "cl_invested": round(cl_i, 2)})
    return cl_exits, cl_buys, cl_pos


if __name__ == "__main__":
    main()
