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


def build_summary() -> str:
    """
    07:00 계좌 요약 — 한 화면에 들어가게. 순서: 총자산 → 성과(오늘·낙폭·SPY 대비) → 배분 → 보유 → 누적 손익.
    매도·매수·체결 같은 "한 일" 은 06:30 장마감 메시지와 목 01:00 매수 메시지에 있다.
    """
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cap      = cfg_live.get("capital", {})
    today_kst = pd.Timestamp.now(tz="Asia/Seoul")
    L = []

    try:
        kis = KISClient.from_config(allow_prod=True)
    except Exception as e:
        return f"📊 <b>US 계좌</b> {today_kst:%m/%d}\n🚨 KIS 초기화 실패: {e}"
    cur_map, bal, err = _safe_get_balance(kis)
    if err:
        return f"📊 <b>US 계좌</b> {today_kst:%m/%d}\n🚨 잔고 조회 실패: {err}"

    cash  = float(bal.get("cash_usd", 0.0) or 0)
    evals = sum(float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", []))
    unsettled = 0.0
    try:
        from live_trading.risk_guard import load_nav
        rows = load_nav("us")
        if rows and rows[-1]["date"][:10] == today_kst.strftime("%Y-%m-%d"):
            unsettled = float(rows[-1].get("unsettled") or 0)
    except Exception:
        rows = []
    total = cash + evals + unsettled
    cash_all = cash + unsettled
    L.append(f"📊 <b>US 계좌</b> {today_kst:%m/%d}  <b>${total:,.0f}</b>")
    cash_s = f"현금 ${cash_all:,.0f} ({cash_all / total * 100 if total else 0:.0f}%)"
    if unsettled:
        cash_s += f" · 결제대기 ${unsettled:,.0f} 포함"
    if bal.get("_cash_failed"):
        cash_s = "🚨 현금 조회 실패 (총자산 부정확)"
    L.append(cash_s)

    # 성과
    try:
        from live_trading.risk_guard import RiskGuard, benchmark_stats
        st = RiskGuard.from_config(market="us").status()
        if not st.get("error") and not st.get("no_history"):
            lim = RiskGuard.from_config(market="us").max_drawdown_pct
            icon = "🛑" if st["drawdown_pct"] <= -lim else ("⚠️" if st["drawdown_pct"] <= -lim * 0.75 else "")
            L.append(f"오늘 {st['daily_pct']:+.1f}% · 낙폭 {st['drawdown_pct']:.1f}% (한도 -{lim:.0f}%) {icon}".rstrip())
        b = benchmark_stats("us")
        if b:
            icon = "🟢" if b["excess_pp"] >= 0 else "🔴"
            L.append(f"{b['since'][5:].replace('-', '/')}~ 계좌 {b['acct_pct']:+.1f}% · SPY {b['spy_pct']:+.1f}% "
                     f"{icon} {b['excess_pp']:+.1f}%p")
    except Exception as e:
        L.append(f"⚠️ 성과 계산 실패: {e}")

    # 배분 (목표 대비)
    core_sym = cap.get("core_symbol", "SPY") if float(cap.get("core_pct", 0) or 0) > 0 else None
    core_val = float(cur_map.get(core_sym, {}).get("eval_amt", 0) or 0) if core_sym else 0.0
    positions = {s: load_live_positions(s) for s in STRATEGIES}
    sleeve_val = {s: sum(float(cur_map.get(sym, {}).get("eval_amt", 0) or 0) for sym in ps)
                  for s, ps in positions.items()}
    parts = []
    if core_sym:
        parts.append(f"{core_sym} {core_val / total * 100 if total else 0:.0f}%/{float(cap['core_pct']):.0f}%")
    for s in STRATEGIES:
        pct = float(cap.get(f"{s}_pct", 0) or 0)
        if pct > 0 or positions[s]:
            parts.append(f"{s.capitalize()} {sleeve_val[s] / total * 100 if total else 0:.0f}%/{pct:.0f}%")
    if parts:
        L.append("배분 " + " · ".join(parts) + "  (현재/목표)")

    # 보유 — 종목당 한 줄
    held_syms = {sym for ps in positions.values() for sym in ps}
    price_data = {}
    if held_syms:
        try:
            from src.fetch.prices import fetch_all
            price_data = fetch_all(list(held_syms), cfg["data"]["start_date"], cfg["data"]["end_date"], min_bars=150)
        except Exception as e:
            log.warning(f"가격 데이터 로드 실패 (손절선 생략): {e}")
    rows_out = []
    if core_sym and core_sym in cur_map:
        i = cur_map[core_sym]
        avg, cur = float(i.get("avg_price", 0) or 0), float(i.get("cur_price", 0) or 0)
        rows_out.append(f"  {core_sym} {int(i.get('qty', 0))}주 {(cur / avg - 1) * 100 if avg else 0:+.1f}% ${float(i.get('eval_amt', 0)):,.0f}")
    total_unreal = 0.0
    for s in STRATEGIES:
        for sym in sorted(positions[s]):
            pos = positions[s][sym]
            if pos.status == "pending_buy":
                rows_out.append(f"  {sym} ⏳매수 대기 {pos.qty}주")
                continue
            i = cur_map.get(sym, {})
            cur = float(i.get("cur_price", pos.fill_price) or pos.fill_price)
            qty = int(i.get("qty", pos.qty) or pos.qty)
            total_unreal += (cur - pos.fill_price) * qty
            pct = (cur / pos.fill_price - 1) * 100 if pos.fill_price else 0.0
            if pos.status == "pending_sell":
                tail = " ⏳매도 중"
            else:
                period = (cfg.get("clenow_strategy", {}).get("ma100_period", 100) if s == "clenow"
                          else cfg.get("weinstein_strategy", {}).get("ma30_period", 30))
                stop = _ma_stop(sym, price_data, period, weekly=(s == "weinstein"))
                tail = f" 손절 ${stop:,.0f} ({(stop / cur - 1) * 100:+.0f}%)" if stop else ""
            rows_out.append(f"  {sym} {qty}주 {pct:+.1f}% ${cur * qty:,.0f}{tail}")
    untracked = [x for x in cur_map if x not in held_syms and x != core_sym and not cur_map[x].get("_pending_sell")]
    if rows_out:
        L.append("")
        L.extend(rows_out)
    if untracked:
        L.append("⚠️ 장부에 없는 보유: " + ", ".join(sorted(untracked)))

    realized = sum(_realized_pnl(s) for s in STRATEGIES)
    L.append("")
    L.append(f"전략 손익 누적 ${realized + total_unreal:+,.0f} (확정 ${realized:+,.0f})")
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
