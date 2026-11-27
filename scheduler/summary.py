"""
scheduler/summary.py
실행 시각: 평일 07:00 KR
역할: KIS 실전 일일 요약 → 텔레그램 전송 (페이퍼 트레이딩과 동일한 구조)

수동 실행:
    python scheduler/summary.py [--print-only]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler._log_helper import setup_file_logger
setup_file_logger("summary")

from live_trading.kis_client import KISClient
from live_trading.risk_guard import benchmark_line
from live_trading.tracker_live import load_live_positions, _trades_file

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

STRATEGIES = ("clenow", "weinstein")   # weinstein 은 청산이 끝나면 섹션이 자동으로 사라진다
STRATEGY_LABELS = {
    "clenow":    "📊 Clenow (모멘텀)",
    "weinstein": "🏭 Weinstein (Stage 2)",
}
ORDER_MAP = Path("live_trading/order_map.json")


def _safe_get_balance(kis):
    try:
        from live_trading.account import with_pending_sells
        bal = with_pending_sells(kis.get_balance())    # 매도 접수 수량은 KIS 잔고에서 빠져 보인다
        cur_map = {p["symbol"]: p for p in bal.get("positions", [])}
        return cur_map, bal, None
    except Exception as e:
        return {}, {"cash_usd": 0, "total_pnl_usd": 0, "positions": []}, str(e)


def _ma_stop(sym, price_data, period_days, weekly=False):
    df = price_data.get(sym)
    if df is None or df.empty:
        return None
    if weekly:
        try:
            from src.strategy.weinstein_stage2 import _resample_weekly
            df = _resample_weekly(df)
        except Exception:
            return None
    ma = df["close"].rolling(period_days).mean().dropna()
    return float(ma.iloc[-1]) if not ma.empty else None


def _today_trades(strategy: str, today_str: str):
    f = _trades_file(strategy)
    if not f.exists():
        return []
    try:
        df = pd.read_csv(f)
    except Exception:
        return []
    col = "exit_date" if "exit_date" in df.columns else "date"
    if col not in df.columns:
        return []
    df = df[df[col].astype(str).str.startswith(today_str)]
    return df.to_dict("records")


def _realized_pnl(strategy: str) -> float:
    f = _trades_file(strategy)
    if not f.exists():
        return 0.0
    try:
        df = pd.read_csv(f)
        if "pnl" not in df.columns:
            return 0.0
        return float(df["pnl"].sum())
    except Exception:
        return 0.0


def _today_orders(today_str: str):
    """order_map.json 에서 오늘 timestamp 의 주문 추출. key = strategy:symbol:signal_date:side"""
    if not ORDER_MAP.exists():
        return {}
    try:
        m = json.loads(ORDER_MAP.read_text(encoding="utf-8"))
    except Exception:
        return {}
    by_strategy = {}
    for key, val in m.items():
        parts = key.split(":")
        if len(parts) != 4:
            continue
        strat, sym, sig_date, side = parts
        ts = val.get("timestamp", "")
        if not ts.startswith(today_str):
            continue
        by_strategy.setdefault(strat, []).append({
            "symbol": sym, "side": side, "qty": val.get("qty", 0),
            "price": val.get("price", 0), "order_no": val.get("order_no", ""),
        })
    return by_strategy


def _trade_period():
    """확정 손익의 기간과 건수 (trades_live_*.csv 의 청산일 기준)."""
    first, last, n = None, None, 0
    for s in STRATEGIES:
        f = _trades_file(s)
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if "exit_date" in df.columns and len(df):
            d = pd.to_datetime(df["exit_date"], errors="coerce").dropna()
            if len(d):
                first = d.min() if first is None else min(first, d.min())
                last = d.max() if last is None else max(last, d.max())
                n += len(d)
    return first, last, n


def _last_wed_buy() -> dict:
    try:
        return json.loads(Path("live_trading/last_wed_buy.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_summary() -> str:
    """
    07:00 계좌 요약. 위에서부터: 총자산과 구성 → SPY 대비 성과(기간 명시) → 보유 종목 → 매매 손익(기간 명시).
    한 줄에 하나의 사실만, 줄임말·기호 대신 문장으로.
    """
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cap      = cfg_live.get("capital", {})
    today_kst = pd.Timestamp.now(tz="Asia/Seoul")
    md = lambda d: f"{pd.Timestamp(d):%m/%d}"
    L = []

    try:
        kis = KISClient.from_config(allow_prod=True)
    except Exception as e:
        return f"📊 <b>US 계좌</b> {today_kst:%m/%d}\n🚨 KIS 초기화 실패: {e}"
    cur_map, bal, err = _safe_get_balance(kis)
    if err:
        return f"📊 <b>US 계좌</b> {today_kst:%m/%d}\n🚨 잔고 조회 실패: {err}"

    from live_trading.risk_guard import RiskGuard, benchmark_stats, load_nav
    try:
        rows = load_nav("us")
    except Exception:
        rows = []
    cash = float(bal.get("cash_usd", 0.0) or 0)
    unsettled = float(rows[-1].get("unsettled") or 0) if rows and rows[-1]["date"][:10] == today_kst.strftime("%Y-%m-%d") else 0.0
    evals = sum(float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", []))
    total = cash + unsettled + evals
    pct = lambda v: f"{v / total * 100:.0f}%" if total else "-"

    core_sym = cap.get("core_symbol", "SPY") if float(cap.get("core_pct", 0) or 0) > 0 else None
    positions = {s: load_live_positions(s) for s in STRATEGIES}
    held_syms = {sym for ps in positions.values() for sym in ps}

    # ── 1. 총자산과 구성 ────────────────────────────────────────────
    L.append(f"📊 <b>US 계좌 {today_kst:%m/%d}</b>")
    L.append(f"총자산 <b>${total:,.0f}</b>")
    if bal.get("_cash_failed"):
        L.append("  🚨 현금 조회 실패 — 총자산 부정확")
    else:
        L.append(f"  현금 ${cash + unsettled:,.0f} ({pct(cash + unsettled)})" + (f" — 결제 대기 매도대금 ${unsettled:,.0f} 포함" if unsettled else ""))
    if core_sym:
        info = cur_map.get(core_sym)
        v = float(info.get("eval_amt", 0) or 0) if info else 0.0
        q = int(info.get("qty", 0) or 0) if info else 0
        line = f"  {core_sym} {q}주 ${v:,.0f} ({pct(v)}, 목표 {float(cap['core_pct']):.0f}%)"
        wb = _last_wed_buy()
        bad = [x for x in wb.get("orders", []) if x.get("symbol") == core_sym and x.get("result") != "ok"]
        if q == 0 and bad:
            line += f" — {md(wb['date'])} 매수 실패"
        L.append(line)
    for s in STRATEGIES:
        target = float(cap.get(f"{s}_pct", 0) or 0)
        ps = positions[s]
        if target <= 0 and not ps:
            continue
        v = sum(float(cur_map.get(sym, {}).get("eval_amt", 0) or 0) for sym in ps)
        L.append(f"  {s.capitalize()} {len(ps)}종목 ${v:,.0f} ({pct(v)}, 목표 {target:.0f}%)")
    if total and (cash + unsettled) / total > 0.5:
        why = []
        try:
            from live_trading.risk_guard import load_spy_closes
            from src.strategy.clenow_momentum import spy_regime_ok
            spy = load_spy_closes(cfg)
            if spy_regime_ok(spy, spy.index[-1]) is False:
                why.append("Clenow 신규 매수 쉬는 중 (SPY 가 200일 평균 아래)")
        except Exception:
            pass
        if core_sym and not cur_map.get(core_sym):
            why.append(f"{core_sym} 는 매주 수요일 총자산의 {float(cap.get('core_max_buy_pct', 15)):.0f}% 씩 나눠 사는 중")
        if why:
            L.append("  현금이 많은 이유: " + " / ".join(why))

    # ── 2. 성과 (기간 명시) ─────────────────────────────────────────
    try:
        b = benchmark_stats("us")
        if b:
            lead = "앞섬" if b["excess_pp"] >= 0 else "뒤처짐"
            icon = "🟢" if b["excess_pp"] >= 0 else "🔴"
            L.append("")
            L.append(f"<b>수익률</b> ({md(b['since'])}~{md(b['last'])}, 실계좌 기록 기준)")
            L.append(f"  계좌 {b['acct_pct']:+.1f}% · SPY {b['spy_pct']:+.1f}%")
            L.append(f"  {icon} 계좌가 SPY 보다 {abs(b['excess_pp']):.1f}%p {lead}")
        st = RiskGuard.from_config(market="us").status()
        if not st.get("error") and not st.get("no_history"):
            lim = RiskGuard.from_config(market="us").max_drawdown_pct
            warn = " 🛑 신규 매수 중단" if st["drawdown_pct"] <= -lim else (" ⚠️" if st["drawdown_pct"] <= -lim * 0.75 else "")
            L.append(f"  최고점 대비 {st['drawdown_pct']:.1f}% (한도 -{lim:.0f}%){warn} · 전일 대비 {st['daily_pct']:+.1f}%")
    except Exception as e:
        L.append(f"⚠️ 성과 계산 실패: {e}")

    # ── 3. 보유 종목 ────────────────────────────────────────────────
    price_data = {}
    if held_syms:
        try:
            from src.fetch.prices import fetch_all
            price_data = fetch_all(list(held_syms), cfg["data"]["start_date"], cfg["data"]["end_date"], min_bars=150)
        except Exception as e:
            log.warning(f"가격 데이터 로드 실패 (손절선 생략): {e}")
    items, unreal = [], 0.0
    if core_sym and core_sym in cur_map:
        i = cur_map[core_sym]
        avg, cur = float(i.get("avg_price", 0) or 0), float(i.get("cur_price", 0) or 0)
        unreal_core = (cur - avg) * int(i.get("qty", 0) or 0)
        items.append(f"  {core_sym} {int(i.get('qty', 0))}주 — 매수가 대비 {(cur / avg - 1) * 100 if avg else 0:+.1f}% (${unreal_core:+,.0f})")
    for s in STRATEGIES:
        for sym in sorted(positions[s]):
            pos = positions[s][sym]
            if pos.status == "pending_buy":
                items.append(f"  {sym} {pos.qty}주 — 매수 체결 대기")
                continue
            i = cur_map.get(sym, {})
            cur = float(i.get("cur_price", pos.fill_price) or pos.fill_price)
            qty = int(i.get("qty", pos.qty) or pos.qty)
            u = (cur - pos.fill_price) * qty
            unreal += u
            chg = (cur / pos.fill_price - 1) * 100 if pos.fill_price else 0.0
            line = f"  {sym} {qty}주 — 매수가 대비 {chg:+.1f}% (${u:+,.0f})"
            if pos.status == "pending_sell":
                line += ", 매도 주문 중"
            else:
                period = (cfg.get("clenow_strategy", {}).get("ma100_period", 100) if s == "clenow"
                          else cfg.get("weinstein_strategy", {}).get("ma30_period", 30))
                stop = _ma_stop(sym, price_data, period, weekly=(s == "weinstein"))
                if stop:
                    line += f", 지금보다 {abs(stop / cur - 1) * 100:.0f}% 내리면 매도 (${stop:,.0f})"
            items.append(line)
    untracked = [x for x in cur_map if x not in held_syms and x != core_sym and not cur_map[x].get("_pending_sell")]
    L.append("")
    L.append(f"<b>보유 종목 {len(items)}개</b>" + (" (나머지는 현금)" if items and total and (cash + unsettled) / total > 0.5 else ""))
    L.extend(items or ["  없음 — 전액 현금"])
    if untracked:
        L.append("  ⚠️ 장부에 없는 보유: " + ", ".join(sorted(untracked)))

    # ── 4. 매매 손익 (기간 명시) ────────────────────────────────────
    realized = sum(_realized_pnl(s) for s in STRATEGIES)
    first, last, n = _trade_period()
    L.append("")
    if n:
        L.append(f"<b>매매 손익</b> (청산 {n}건, {md(first)}~{md(last)})")
        L.append(f"  확정 ${realized:+,.0f} · 보유 중 평가 ${unreal:+,.0f}")
    else:
        L.append(f"<b>매매 손익</b> — 청산 기록 없음 · 보유 중 평가 ${unreal:+,.0f}")
    return "\n".join(L)


def main(print_only: bool = False):
    msg = build_summary()
    log.info("\n" + msg)

    if print_only:
        return

    try:
        cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        token    = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id  = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            if telegram.send(msg, token=token, chat_id=chat_id):
                log.info("[summary] 텔레그램 전송 완료")
            else:
                log.error("[summary] 텔레그램 전송 실패 (응답 오류)")
        else:
            log.warning("[summary] telegram_token/chat_id 미설정 - 전송 스킵")
    except Exception as e:
        log.error(f"[summary] 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--print-only", action="store_true", help="텔레그램 전송 없이 콘솔 출력만")
    args = p.parse_args()
    main(print_only=args.print_only)
