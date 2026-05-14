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

from live_trading.kis_client import KISClient
from live_trading.account import slippage_report
from live_trading.tracker_live import load_live_positions, _trades_file

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

STRATEGIES = ("phase4", "clenow", "weinstein")
STRATEGY_LABELS = {
    "phase4":    "📈 Phase 4 (추세추종)",
    "clenow":    "📊 Clenow (모멘텀)",
    "weinstein": "🏭 Weinstein (Stage 2)",
}
ORDER_MAP = Path("live_trading/order_map.json")


def _safe_get_balance(kis):
    try:
        bal = kis.get_balance()
        cur_map = {p["symbol"]: p for p in bal.get("positions", [])}
        return cur_map, bal, None
    except Exception as e:
        return {}, {"cash_usd": 0, "total_eval_usd": 0, "positions": []}, str(e)


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
    if "date" not in df.columns:
        return []
    df = df[df["date"].astype(str).str.startswith(today_str)]
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
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cap      = cfg_live.get("capital", {})

    today_kst = pd.Timestamp.now(tz="Asia/Seoul")
    today_str = today_kst.strftime("%Y-%m-%d")

    L = []
    def line(s=""): L.append(s)
    def bold(s): return f"<b>{s}</b>"

    line(f"📅 {bold(today_str + ' KIS Live')}")

    # KIS 잔고
    try:
        kis = KISClient.from_config(allow_prod=True)
    except Exception as e:
        line(f"⚠️ KIS 클라이언트 초기화 실패: {e}")
        return "\n".join(L)

    cur_map, bal, err = _safe_get_balance(kis)
    if err:
        line(f"⚠️ 잔고 조회 실패: {err}")
    else:
        cash     = bal.get("cash_usd", 0.0)
        sum_eval = sum(p["eval_amt"] for p in bal.get("positions", []))
        pnl_amt  = bal.get("total_eval_usd", 0.0)
        line(f"잔고: 예수금 ${cash:,.2f} | 평가 ${sum_eval:,.2f} | 손익 ${pnl_amt:+,.2f}")

    # 보유 종목만 가격 데이터 로드 (stop 계산용)
    held_syms = set()
    for strat in STRATEGIES:
        held_syms.update(load_live_positions(strat).keys())

    price_data = {}
    if held_syms:
        try:
            from src.fetch.prices import fetch_all
            price_data = fetch_all(list(held_syms),
                                   cfg["data"]["start_date"],
                                   cfg["data"]["end_date"],
                                   min_bars=150)
        except Exception as e:
            log.warning(f"가격 데이터 로드 실패 (stop 생략): {e}")

    today_orders = _today_orders(today_str)

    total_unreal   = 0.0
    total_realized = 0.0

    # 추적된 종목 모두 모아두기 (KIS 잔고 vs 로컬 비교용)
    all_tracked = set()

    # auto_allocate 모드면 KIS 총자산 기준 동적 예산 계산
    auto = bool(cap.get("auto_allocate"))
    total_assets = 0.0
    if auto and not err:
        total_assets = cash + sum_eval
        buffer = float(cap.get("buffer_pct", 1.0)) / 100.0
        usable = total_assets * (1.0 - buffer)

    def strategy_budget(s):
        if auto and not err:
            pct = float(cap.get(f"{s}_pct", 0)) / 100.0
            return usable * pct
        return float(cap.get(f"{s}_usd", 0) or 0)

    for strat in STRATEGIES:
        positions = load_live_positions(strat)
        all_tracked.update(positions.keys())
        cap_usd = strategy_budget(strat)
        max_pos = cap.get(f"{strat}_max_positions", 0)

        # 자본 0 + 보유 0 + 오늘 주문 0 → 섹션 완전 생략
        if cap_usd <= 0 and not positions and not today_orders.get(strat):
            continue

        label = STRATEGY_LABELS[strat]
        line()
        line(bold(f"━━━ {label} ━━━"))
        if cap_usd > 0:
            mode_tag = " (auto)" if auto else ""
            line(f"배분{mode_tag}: ${cap_usd:.2f} / {max_pos}종목 = ${cap_usd/max(max_pos,1):.2f}/종목")
        else:
            line("배분: 페이퍼 전용 (실전 자본 0)")

        line(bold(f"보유 {len(positions)}종목"))
        s_unreal = 0.0
        for sym in sorted(positions.keys()):
            pos      = positions[sym]
            cur_info = cur_map.get(sym, {})
            cur      = float(cur_info.get("cur_price", pos.fill_price))
            qty      = int(cur_info.get("qty", pos.qty))
            unreal   = (cur - pos.fill_price) * qty
            s_unreal += unreal
            pct      = (cur / pos.fill_price - 1) * 100 if pos.fill_price > 0 else 0.0
            sign     = "+" if unreal >= 0 else ""

            if strat == "clenow":
                stop = _ma_stop(sym, price_data, cfg.get("clenow_strategy", {}).get("ma100_period", 100))
            elif strat == "weinstein":
                stop = _ma_stop(sym, price_data, cfg.get("weinstein_strategy", {}).get("ma30_period", 30), weekly=True)
            else:
                stop = None
            stop_s = f" | stop ${stop:.2f}" if stop is not None else ""
            line(f"  {sym}: {qty}주 ${pos.fill_price:.2f}→${cur:.2f} ({sign}{pct:.1f}%) | 평가 ${cur*qty:,.2f}{stop_s}")

        # 오늘 주문 (order_map.json 기준)
        orders_today = today_orders.get(strat, [])
        if orders_today:
            line(bold(f"오늘 주문 {len(orders_today)}건"))
            for o in orders_today:
                arrow = "🔔" if o["side"] == "BUY" else "💸"
                line(f"  {arrow} {o['side']} {o['symbol']} x{o['qty']} @${o['price']:.2f}")

        # 오늘 청산 (trades_live_*.csv 가 있을 때만)
        trades_today = _today_trades(strat, today_str)
        if trades_today:
            line(bold(f"오늘 청산 {len(trades_today)}건"))
            for t in trades_today:
                pnl    = float(t.get("pnl", 0) or 0)
                icon   = "✅" if pnl >= 0 else "❌"
                reason = t.get("reason", "")
                line(f"  {icon} {t.get('symbol', '')} [{reason}] ${pnl:+,.2f}")

        # 누적 손익
        s_realized = _realized_pnl(strat)
        line(f"누적: 확정 ${s_realized:+,.2f} / 미실현 ${s_unreal:+,.2f}")
        total_unreal   += s_unreal
        total_realized += s_realized

    # 미할당 KIS 포지션 (전략에 등록되지 않은 보유)
    untracked = [sym for sym in cur_map if sym not in all_tracked]
    if untracked:
        line()
        line(bold("━━━ ⚠️  미할당 KIS 보유 ━━━"))
        for sym in sorted(untracked):
            info = cur_map[sym]
            cur  = float(info.get("cur_price", 0))
            avg  = float(info.get("avg_price", 0))
            qty  = int(info.get("qty", 0))
            ev   = float(info.get("eval_amt", 0))
            pct  = (cur / avg - 1) * 100 if avg > 0 else 0.0
            sign = "+" if cur >= avg else ""
            line(f"  {sym}: {qty}주 ${avg:.2f}→${cur:.2f} ({sign}{pct:.1f}%) | 평가 ${ev:,.2f}")
        line("→ positions_live_*.json 에 수동 등록 필요 (전략 자동 청산 대상에서 누락 중)")

    # 슬리피지
    line()
    sr = slippage_report()
    if sr.get("records", 0) > 0:
        line(f"슬리피지 누적 {sr['records']}건: 평균 {sr['mean_pct']:+.3f}% (σ {sr['std_pct']:.3f}%)")

    # 합계
    line()
    grand = total_realized + total_unreal
    line(bold(f"━━━ 합계: ${grand:+,.2f} (확정 ${total_realized:+,.2f} / 미실현 ${total_unreal:+,.2f}) ━━━"))

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
            telegram.send(msg, token=token, chat_id=chat_id)
            log.info("[summary] 텔레그램 전송 완료")
        else:
            log.warning("[summary] telegram_token/chat_id 미설정 - 전송 스킵")
    except Exception as e:
        log.error(f"[summary] 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--print-only", action="store_true", help="텔레그램 전송 없이 콘솔 출력만")
    args = p.parse_args()
    main(print_only=args.print_only)
