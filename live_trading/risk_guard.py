"""
Phase D — 리스크 가드 (실계좌 NAV 기준, 시장별 분리)

체크 순서 (모든 신규 주문 전 호출):
    guard = RiskGuard.from_config(market="us")
    ok, reason = guard.check()
    if not ok:
        log.warning(f"주문 차단: {reason}")

NAV 는 daily_close 가 매일 record_nav() 로 live_trading/nav_live_{market}.csv 에 기록.
입출금은 낙폭으로 오판되지 않도록 unit_nav(시간가중) 로 계산 — 입출금 시 반드시:
    python -m live_trading.risk_guard --cashflow 1000 --market us    # 입금 +, 출금 -

2026-10 개정:
  - 페이퍼 NAV(paper_trading/daily_nav.csv) 대신 실계좌 NAV 사용 (페이퍼는 실계좌와 무관했고
    로컬 사본은 GHA pull 누락으로 석 달 정체돼 있었음)
  - US/KR 분리 (KR 매수가 US 페이퍼 NAV·USD 자본으로 판정되던 문제)
  - 계산 실패 시 통과 → 차단 (fail-closed). 청산은 가드와 무관하게 항상 허용
  - 연속 손실 규칙 삭제: 승률 31% 추세추종에서 5연속 손실은 흔하고(≈16%),
    손절 연속 뒤의 큰 추세가 수익원이라 그때 진입을 막으면 전략이 깨짐
"""
from __future__ import annotations

import csv
import json
import logging
from bisect import bisect_left
from datetime import date
from pathlib import Path

import yaml

LIVE_DIR    = Path("live_trading")
KILL_SWITCH = LIVE_DIR / "KILL_SWITCH"
CONFIG_PATH = Path("config_live.yaml")

# 마지막 NAV 기록이 이보다 오래되면 판단 근거가 없다고 보고 차단 (주말+연휴 여유)
NAV_STALE_DAYS = 5
# 이 낙폭부터 일일 메시지에 경고 표시 (한도의 75%)
WARN_RATIO = 0.75

log = logging.getLogger("kis")

# flow_ids: 그 행의 flow 에 반영된 입출금 항목 ID (|로 구분) — "어느 입출금이 어느 NAV 에 들어갔는지" 의 증거
NAV_FIELDS = ["date", "total", "cash", "flow", "unit_nav", "unsettled", "spy", "flow_ids"]

# SPY 종가를 붙일 때 기록일(KST)과 직전 세션 사이 허용 간격 (금→월 3일, 연휴 낀 주말 4일)
BENCH_MAX_GAP_DAYS = 5


def nav_path(market: str) -> Path:
    return LIVE_DIR / f"nav_live_{market}.csv"


def _flow_path(market: str) -> Path:
    return LIVE_DIR / f"cashflow_pending_{market}.json"


def _unsettled_path(market: str) -> Path:
    return LIVE_DIR / f"unsettled_sells_{market}.json"


def add_unsettled(market: str, session: str, amount: float, symbol: str = "") -> None:
    """
    체결됐지만 결제 전인 매도 대금 등록 (session = 체결된 NYSE 세션 날짜).
    cash 는 KIS 주문가능금액이라 결제(T+1) 전 매도 대금이 빠져 있다. 평가액은 매도 즉시
    빠지므로 이걸 더하지 않으면 매도 다음 날 가짜 낙폭이 생긴다
    (2026-10-21 DELL $3,199 → 낙폭 -20.01% 오판으로 주간 매수 통째로 건너뜀, 10-26 AMZN -7.87%).
    """
    p = _unsettled_path(market)
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if any(x["session"] == session and x["symbol"] == symbol for x in items):
        return   # 같은 날 재실행
    items.append({"session": session, "symbol": symbol, "amount": round(amount, 2)})
    from live_trading.tracker_live import atomic_write_text
    atomic_write_text(p, json.dumps(items, indent=2))     # 직접 덮어쓰면 쓰다 끊겼을 때 기존 장부까지 깨진다


def _take_unsettled(market: str, session: str | None) -> float:
    """현재 세션(session)에 체결된 대금만 미결제로 본다. 다음 세션이 지나면 결제됐으니 정리."""
    p = _unsettled_path(market)
    if session is None or not p.exists():
        return 0.0
    all_items = json.loads(p.read_text(encoding="utf-8"))
    items = [x for x in all_items if x["session"] >= session]
    if len(items) != len(all_items):
        # 결제가 끝난 항목 정리 — 실패해도 금액 계산에는 영향이 없으므로 넘어간다 (다음에 다시 정리)
        try:
            from live_trading.tracker_live import atomic_write_text
            atomic_write_text(p, json.dumps(items, indent=2))
        except OSError as e:
            log.warning(f"[nav] 미결제 장부 정리 실패 (다음 실행에서 재시도): {e}")
    return sum(x["amount"] for x in items)


def load_nav(market: str) -> list[dict]:
    """NAV 이력. 파일 없음 = 빈 리스트, 파일 손상 = 예외 (호출측이 차단 판단)."""
    p = nav_path(market)
    if not p.exists() or p.stat().st_size == 0:
        return []
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["total"]    = float(r["total"])
        r["unit_nav"] = float(r["unit_nav"])
        r["spy"]      = float(r["spy"]) if r.get("spy") not in (None, "") else None
    return rows


def _write_nav(market: str, rows: list[dict]) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    import io
    from live_trading.tracker_live import atomic_write_text
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=NAV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    atomic_write_text(nav_path(market), buf.getvalue())


def add_cashflow(market: str, amount: float) -> float:
    """입출금 예약. 다음 record_nav 에서 unit_nav 계산 시 제외된다."""
    p = _flow_path(market)
    pending = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    import uuid
    pending.append({"id": uuid.uuid4().hex[:12], "date": date.today().isoformat(), "amount": amount})
    from live_trading.tracker_live import atomic_write_text
    atomic_write_text(p, json.dumps(pending, indent=2))
    return sum(x["amount"] for x in pending)


def record_nav(market: str, balance: dict, today: str | None = None,
               session: str | None = None) -> dict | None:
    """
    KIS 잔고로 오늘 NAV 기록 (같은 날짜 재실행 시 덮어씀).
    total = 주문가능금액 + 평가액 + 미결제 매도대금 (session 을 주면 add_unsettled 장부에서)
    unit_nav_t = unit_nav_{t-1} × (total_t − flow_t) / total_{t-1}
    잔고 조회 실패(_failed)면 기록하지 않고 None — 오염된 0 을 남기지 않는다.
    """
    if balance.get("_failed"):
        log.error(f"[nav] {market} 잔고 조회 실패 — NAV 기록 생략")
        return None
    cash_key = "cash_krw" if market == "kr" else "cash_usd"
    cash  = float(balance.get(cash_key, 0) or 0)
    unsettled = _take_unsettled(market, session)
    total = cash + unsettled + sum(float(p.get("eval_amt", 0) or 0) for p in balance.get("positions", []))
    today = today or date.today().isoformat()

    all_rows = load_nav(market)
    rows = [r for r in all_rows if r["date"] != today]
    # 입출금은 정확히 한 번만 반영한다. 근거는 NAV 행 자체에 남긴다 (flow_ids = 그 행에 반영된 항목 ID).
    # NAV 파일은 통째로 교체되므로 "flow 금액" 과 "어떤 항목이 들어갔는지" 가 항상 함께 저장된다.
    #   · 다른 날짜의 행에 ID 가 있는 항목 = 반영 끝 → 대기 파일에서 지운다 (실패해도 다시 더해지지 않음)
    #   · 어느 행에도 ID 가 없는 항목 = 미반영 → 오늘 행에 넣는다 (NAV 저장이 실패했던 항목 포함)
    #   · 오늘 행에 이미 들어간 항목은 오늘 안에는 지우지 않고 다시 합산 → 같은 날 재기록에도 flow 유지
    #   · 오늘 행의 flow 중 ID 로 설명되지 않는 부분(이 방식 도입 전에 기록된 금액)은 그대로 이어받는다
    # 예전 방식들의 사고: 재기록 때 출금 $3,000 이 손실로 바뀜(-30%), 정리 실패 시 두 번 반영(unit_nav 1.3),
    # 날짜 표시만 보고 반영 끝으로 오인(다음 날 0.7).
    import uuid
    from live_trading.tracker_live import atomic_write_text
    fp = _flow_path(market)
    items = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
    if any(not x.get("id") for x in items):          # 예전 형식 항목에 ID 부여
        for x in items:
            x.setdefault("id", uuid.uuid4().hex[:12])
        atomic_write_text(fp, json.dumps(items, indent=2))
    _ids = lambda r: {i for i in str(r.get("flow_ids") or "").split("|") if i}
    applied_elsewhere = set().union(*[_ids(r) for r in rows]) if rows else set()
    todays = [x for x in items if x["id"] not in applied_elsewhere]
    base = 0.0
    for r in all_rows:
        if r["date"] == today:
            try:
                prev_flow = float(r.get("flow") or 0)
            except (TypeError, ValueError):
                prev_flow = 0.0
            prev_ids = _ids(r)
            base = prev_flow - sum(float(x["amount"]) for x in items if x["id"] in prev_ids)
    flow = base + sum(float(x["amount"]) for x in todays)
    flow_ids = "|".join(x["id"] for x in todays)

    if rows and rows[-1]["total"] > 0:
        unit = rows[-1]["unit_nav"] * (total - flow) / rows[-1]["total"]
    else:
        unit = 1.0
    row = {"date": today, "total": round(total, 2), "cash": round(cash, 2),
           "flow": round(flow, 2), "unit_nav": round(unit, 6), "unsettled": round(unsettled, 2),
           "flow_ids": flow_ids}
    rows.append(row)

    _write_nav(market, rows)
    # 다른 날짜 행에 반영이 끝난 항목 정리 — NAV 저장 뒤에 한다. 실패해도 위 규칙상 다시 반영되지 않는다.
    if len(todays) != len(items):
        try:
            atomic_write_text(fp, json.dumps(todays, indent=2))
        except OSError as e:
            log.warning(f"[nav] 입출금 대기 파일 정리 실패 (다음 실행에서 재시도): {e}")
    return row


# ── SPY 벤치마크 ─────────────────────────────────────────────────────────
# US 판정 기준이 "SPY 보유 대비" 인데 NAV 파일에 비교 대상이 없었다 (2026-11 추가).
# 각 NAV 행에 "그 기록이 반영한 세션" 의 SPY 수정종가를 붙이고, 같은 구간 수익률·MDD 를 비교한다.

def load_spy_closes(cfg: dict | None = None):
    """SPY 수정종가 Series (배당 포함 총수익 기준). 실패하면 예외."""
    from src.fetch.prices import fetch_prices
    if cfg is None:
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    data = cfg.get("data", {})
    df = fetch_prices("SPY", data.get("start_date", "2015-01-01"), data.get("end_date"), refresh=True)
    if df is None or df.empty:
        raise RuntimeError("SPY 가격 조회 실패 (빈 응답)")
    return df["close"]


def update_benchmark(market: str, closes, session: str | None = None) -> int:
    """
    NAV 각 행의 spy 열을 채운다 (매번 전 행 재계산 — 배당 수정으로 과거 종가가 바뀌어도 일관).
    행 날짜는 daily_close 실행일(KST)이라 그 직전 NYSE 세션 종가를 쓴다.
    session = 방금 마감한 세션(YYYY-MM-DD). 주면 그 이후 봉(장중 미확정)은 무시하고,
    오늘 행은 SPY 데이터가 그 세션까지 와 있을 때만 채운다 (밀린 종가를 오늘 값으로 쓰지 않음).
    반환: 값이 채워진 행 수.
    """
    rows = load_nav(market)
    if not rows or closes is None or len(closes) == 0:
        return 0
    bars = sorted((str(d)[:10], float(v)) for d, v in closes.items() if v == v)
    if session:
        bars = [b for b in bars if b[0] <= session]
    days = [b[0] for b in bars]
    today = date.today().isoformat()
    filled = 0
    for r in rows:
        d = r["date"][:10]
        k = bisect_left(days, d) - 1
        if k < 0:
            continue
        if (date.fromisoformat(d) - date.fromisoformat(days[k])).days > BENCH_MAX_GAP_DAYS:
            continue
        if session and d == today and days[k] != session:
            r["spy"] = None
            continue
        r["spy"] = round(bars[k][1], 4)
        filled += 1
    _write_nav(market, rows)
    return filled


def _mdd_pct(xs: list[float]) -> float:
    peak, worst = xs[0], 0.0
    for x in xs:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    return worst * 100


def benchmark_stats(market: str = "us") -> dict | None:
    """SPY 값이 있는 첫 행을 기준일로 계좌(unit_nav) vs SPY. 비교할 행이 2개 미만이면 None."""
    rows = [r for r in load_nav(market) if r.get("spy")]
    if len(rows) < 2:
        return None
    acct = [r["unit_nav"] for r in rows]
    spy  = [r["spy"] for r in rows]
    a = (acct[-1] / acct[0] - 1) * 100
    s = (spy[-1] / spy[0] - 1) * 100
    return {"since": rows[0]["date"][:10], "last": rows[-1]["date"][:10], "n": len(rows),
            "acct_pct": a, "spy_pct": s, "excess_pp": a - s,
            "acct_mdd": _mdd_pct(acct), "spy_mdd": _mdd_pct(spy)}


def benchmark_line(market: str = "us") -> str:
    """일일 텔레그램용 한 줄. 비교 불가면 빈 문자열 (메시지를 깨지 않는다)."""
    try:
        s = benchmark_stats(market)
    except Exception as e:
        return f"📊 SPY 비교 계산 실패: {e}"
    if not s:
        return ""
    icon = "🟢" if s["excess_pp"] >= 0 else "🔴"
    return (f"📊 SPY 대비 ({s['since'][5:].replace('-', '/')}~, {s['n']}일): "
            f"계좌 {s['acct_pct']:+.2f}% vs SPY {s['spy_pct']:+.2f}% → {icon} {s['excess_pp']:+.2f}%p"
            f" | MDD {s['acct_mdd']:.1f}% vs {s['spy_mdd']:.1f}%")


class RiskGuard:
    def __init__(self, cfg: dict, market: str = "us"):
        self.market = market
        # 시장별 섹션(kr.risk_guard) 우선, 없으면 최상위 risk_guard
        rg = {**cfg.get("risk_guard", {}), **((cfg.get(market) or {}).get("risk_guard") or {})}
        self.daily_loss_pct   = rg.get("daily_loss_pct", 1.5)
        self.max_drawdown_pct = rg.get("max_drawdown_pct", 20.0)
        self.max_order_pct    = rg.get("max_order_pct_of_capital", 50)
        cap = (cfg.get(market) or {}).get("capital") or cfg.get("capital", {})
        self._fallback_capital = cap.get("total_krw" if market == "kr" else "total_usd", 10000)

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH, market: str = "us") -> "RiskGuard":
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(cfg, market=market)

    def status(self) -> dict:
        """현재 낙폭/당일손익. 계산 불가면 error 에 사유."""
        try:
            rows = load_nav(self.market)
        except Exception as e:
            return {"error": f"NAV 파일 읽기 실패: {e}"}
        if not rows:
            return {"error": "", "no_history": True}
        last = rows[-1]
        age = (date.today() - date.fromisoformat(last["date"][:10])).days
        peak = max(r["unit_nav"] for r in rows)
        dd = (last["unit_nav"] / peak - 1) * 100 if peak > 0 else 0.0
        daily = ((last["unit_nav"] / rows[-2]["unit_nav"] - 1) * 100
                 if len(rows) >= 2 and rows[-2]["unit_nav"] > 0 else 0.0)
        return {"error": "", "no_history": False, "drawdown_pct": dd, "daily_pct": daily,
                "last_date": last["date"], "age_days": age, "total": last["total"]}

    def check(self, allow_exits: bool = False) -> tuple[bool, str]:
        """
        신규 진입 가능 여부. 판단 근거가 없으면(NAV 손상·정체) 차단한다.
        allow_exits=True 면 당일 손실 한도는 무시 (청산 경로 호환용).
        """
        if KILL_SWITCH.exists():
            return False, "KILL_SWITCH 파일 존재 — 수동 정지"

        s = self.status()
        if s["error"]:
            return False, s["error"] + " — 신규 매수 차단 (fail-closed)"
        if s["no_history"]:
            log.warning(f"[guard] {self.market} NAV 이력 없음 — 첫 기록 전이라 통과")
            return True, ""
        if s["age_days"] > NAV_STALE_DAYS:
            return False, (f"NAV 기록 정체 (마지막 {s['last_date']}, {s['age_days']}일 전) — "
                           f"신규 매수 차단")
        if s["drawdown_pct"] <= -self.max_drawdown_pct:
            return False, (f"누적 낙폭 {s['drawdown_pct']:.2f}% — 신규 매수 중단 "
                           f"(한도 -{self.max_drawdown_pct}%)")
        if not allow_exits and s["daily_pct"] <= -self.daily_loss_pct:
            return False, (f"당일 손실 {s['daily_pct']:.2f}% — 신규 진입 차단 "
                           f"(한도 -{self.daily_loss_pct}%)")
        return True, ""

    def summary_line(self) -> str:
        """일일 텔레그램용 한 줄. 경고 구간이면 ⚠️."""
        s = self.status()
        if s["error"]:
            return f"🚨 리스크 가드 판단 불가: {s['error']}"
        if s["no_history"]:
            return "리스크: NAV 이력 없음 (첫 기록 대기)"
        dd, lim = s["drawdown_pct"], self.max_drawdown_pct
        icon = "🛑" if dd <= -lim else ("⚠️" if dd <= -lim * WARN_RATIO else "📉")
        return f"{icon} 낙폭 {dd:.2f}% (한도 -{lim:.0f}%) | 당일 {s['daily_pct']:+.2f}%"

    def check_order_size(self, order_amt: float) -> tuple[bool, str]:
        """단일 주문 금액 한도 체크 (최근 NAV 총자산 기준, 없으면 config)."""
        s = self.status()
        capital = s.get("total") or self._fallback_capital
        limit = capital * self.max_order_pct / 100
        if order_amt > limit:
            return False, (f"주문 금액 {order_amt:,.0f} > 한도 {limit:,.0f} "
                           f"(자본의 {self.max_order_pct}%)")
        return True, ""


def _cli():
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="us", choices=["us", "kr"])
    p.add_argument("--cashflow", type=float, help="입금(+)/출금(-) 금액 — 다음 NAV 기록에 반영")
    p.add_argument("--status", action="store_true")
    p.add_argument("--benchmark", action="store_true",
                   help="SPY 종가를 NAV 이력에 채우고 SPY 대비 성과 출력 (US)")
    args = p.parse_args()
    if args.benchmark:
        n = update_benchmark("us", load_spy_closes())
        print(f"SPY 종가 {n}행 반영")
        print(benchmark_line("us") or "비교할 NAV 이력이 부족함")
        return
    if args.cashflow is not None:
        total = add_cashflow(args.market, args.cashflow)
        print(f"{args.market} 입출금 예약: {args.cashflow:+,.2f} (미반영 합계 {total:+,.2f})")
    if args.status or args.cashflow is None:
        g = RiskGuard.from_config(market=args.market)
        print(g.summary_line())
        print("check:", g.check())


if __name__ == "__main__":
    _cli()
