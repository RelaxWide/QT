"""
paper_trading/report.py
run_daily.py 실행 후 호출 — Claude 분석용 일일 리포트를 daily_report.md에 저장.
"""
import csv
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "wr": 0.0, "pf": 0.0,
                "total_pnl": 0.0, "streak": 0, "streak_type": "-", "recent10": "-"}
    total        = len(trades)
    wins         = sum(1 for t in trades if float(t["pnl"]) > 0)
    gross_profit = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gross_loss   = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) < 0))
    pf           = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    total_pnl    = sum(float(t["pnl"]) for t in trades)

    streak, streak_type = 0, "-"
    for t in reversed(trades):
        w = float(t["pnl"]) > 0
        if streak == 0:
            streak_type = "W" if w else "L"
            streak = 1
        elif (w and streak_type == "W") or (not w and streak_type == "L"):
            streak += 1
        else:
            break

    recent10 = " ".join("W" if float(t["pnl"]) > 0 else "L" for t in trades[-10:])

    return {
        "total": total, "wins": wins, "wr": wins / total,
        "pf": round(pf, 2), "total_pnl": round(total_pnl, 2),
        "streak": streak, "streak_type": streak_type, "recent10": recent10,
    }


def compute_drawdown(trades: list[dict], initial: float = 100000.0) -> tuple[float, float]:
    """(현재 낙폭 %, 최대 낙폭 %)"""
    equity, peak, max_dd = initial, initial, 0.0
    for t in trades:
        equity += float(t["pnl"])
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak
        if dd < max_dd:
            max_dd = dd
    cur_dd = (equity - peak) / peak if peak > 0 else 0.0
    return round(cur_dd * 100, 2), round(max_dd * 100, 2)


def strategy_block(name: str, m: dict, cur_dd: float, max_dd: float,
                   positions: dict, wr_gate: float, pf_gate: float) -> list[str]:
    L = []
    L.append(f"### {name}")
    L.append(f"보유 {len(positions)}종목" + (f": {', '.join(sorted(positions.keys()))}" if positions else ""))
    L.append("")

    if m["total"] == 0:
        L.append("누적 거래: 없음")
    else:
        L.append(f"누적: {m['total']}건 | WR {m['wr']:.1%} | PF {m['pf']} | PnL ${m['total_pnl']:+,.0f}")
        L.append(f"낙폭: 현재 {cur_dd:+.1f}% | 최대 {max_dd:.1f}%")
        L.append(f"스트릭: {m['streak_type']} {m['streak']}연속")
        L.append(f"최근 10건: {m['recent10']}")

    warns = []
    if m["total"] >= 10:
        if m["wr"] < wr_gate:
            warns.append(f"⚠️ WR {m['wr']:.1%} < 게이트 {wr_gate:.0%}")
        if m["pf"] < pf_gate:
            warns.append(f"⚠️ PF {m['pf']} < 게이트 {pf_gate}")
    if cur_dd < -10:
        warns.append(f"🚨 현재 낙폭 {cur_dd:.1f}% (한도 -15%)")
    if m["streak_type"] == "L" and m["streak"] >= 5:
        warns.append(f"⚠️ {m['streak']}연속 손절 중")
    if warns:
        L.append("")
        L.extend(warns)

    return L


def generate_report() -> str:
    base = Path("paper_trading")

    p4_trades = load_trades(base / "trades.csv")
    cl_trades = load_trades(base / "trades_clenow.csv")
    w_trades  = load_trades(base / "trades_weinstein.csv")
    p4_pos    = load_json(base / "positions.json", {})
    cl_pos    = load_json(base / "positions_clenow.json", {})
    w_pos     = load_json(base / "positions_weinstein.json", {})
    pending   = load_json(base / "pending.json", [])

    p4_m = compute_metrics(p4_trades)
    cl_m = compute_metrics(cl_trades)
    w_m  = compute_metrics(w_trades)

    p4_cur_dd, p4_max_dd = compute_drawdown(p4_trades)
    cl_cur_dd, cl_max_dd = compute_drawdown(cl_trades)
    w_cur_dd,  w_max_dd  = compute_drawdown(w_trades)

    L = []

    L.append(f"# QT Paper Trading 일일 리포트")
    L.append(f"날짜: {date.today().isoformat()}")
    L.append("")

    # ── 전략별 현황 ──────────────────────────────────────────────────
    L.append("## 전략별 현황")
    L.append("")
    L.extend(strategy_block("Phase 4 (추세추종)", p4_m, p4_cur_dd, p4_max_dd, p4_pos, 0.33, 1.5))
    L.append("")
    L.extend(strategy_block("Clenow (모멘텀)", cl_m, cl_cur_dd, cl_max_dd, cl_pos, 0.55, 1.5))
    L.append("")
    L.extend(strategy_block("Weinstein (Stage 2)", w_m, w_cur_dd, w_max_dd, w_pos, 0.31, 1.5))
    L.append("")

    # ── Phase 4 보유 포지션 상세 ──────────────────────────────────────
    if p4_pos:
        L.append("## Phase 4 보유 포지션 상세")
        L.append("| 종목 | 진입일 | 진입가 | stop | targets_hit |")
        L.append("|------|--------|--------|------|-------------|")
        for sym, pos in p4_pos.items():
            L.append(f"| {sym} | {pos['entry_date']} | ${pos['entry_price']:.2f} | ${pos['stop_current']:.2f} | {pos['targets_hit']} |")
        L.append("")

    # ── 내일 진입 예정 ────────────────────────────────────────────────
    if pending:
        L.append("## 내일 진입 예정 (Phase 4)")
        L.append("| 종목 | 예상가 | stop | R |")
        L.append("|------|--------|------|---|")
        for p in pending:
            L.append(f"| {p['symbol']} | ${p['entry_price_est']:.2f} | ${p['stop']:.2f} | ${p['r']:.2f} |")
        L.append("")

    # ── 최근 거래 내역 (전 전략 합산, 최근 20건) ─────────────────────
    L.append("## 최근 거래 내역 (최근 20건)")
    all_recent = (
        [("Phase4", t) for t in p4_trades] +
        [("Clenow", t) for t in cl_trades] +
        [("Weinstein", t) for t in w_trades]
    )
    all_recent.sort(key=lambda x: x[1].get("date", ""))
    all_recent = all_recent[-20:]

    if all_recent:
        L.append("| 날짜 | 전략 | 종목 | PnL | R | 사유 |")
        L.append("|------|------|------|-----|---|------|")
        for strategy, t in all_recent:
            pnl    = float(t["pnl"])
            icon   = "✅" if pnl > 0 else "❌"
            r_str  = t.get("r_multiple", "-")
            reason = t.get("exit_reason") or t.get("reason", "")
            L.append(f"| {t['date']} | {strategy} | {t['symbol']} | {icon} ${pnl:+,.0f} | {r_str} | {reason} |")
    else:
        L.append("거래 없음")

    return "\n".join(L)


if __name__ == "__main__":
    report = generate_report()
    out = Path("paper_trading/daily_report.md")
    out.write_text(report, encoding="utf-8")
    print(f"✅ 리포트 저장: {out}")
