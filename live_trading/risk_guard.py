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

NAV_FIELDS = ["date", "total", "cash", "flow", "unit_nav"]


def nav_path(market: str) -> Path:
    return LIVE_DIR / f"nav_live_{market}.csv"


def _flow_path(market: str) -> Path:
    return LIVE_DIR / f"cashflow_pending_{market}.json"


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
    return rows


def add_cashflow(market: str, amount: float) -> float:
    """입출금 예약. 다음 record_nav 에서 unit_nav 계산 시 제외된다."""
    p = _flow_path(market)
    pending = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    pending.append({"date": date.today().isoformat(), "amount": amount})
    LIVE_DIR.mkdir(exist_ok=True)
    p.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    return sum(x["amount"] for x in pending)


def record_nav(market: str, balance: dict, today: str | None = None) -> dict | None:
    """
    KIS 잔고로 오늘 NAV 기록 (같은 날짜 재실행 시 덮어씀).
    unit_nav_t = unit_nav_{t-1} × (total_t − flow_t) / total_{t-1}
    잔고 조회 실패(_failed)면 기록하지 않고 None — 오염된 0 을 남기지 않는다.
    """
    if balance.get("_failed"):
        log.error(f"[nav] {market} 잔고 조회 실패 — NAV 기록 생략")
        return None
    cash_key = "cash_krw" if market == "kr" else "cash_usd"
    cash  = float(balance.get(cash_key, 0) or 0)
    total = cash + sum(float(p.get("eval_amt", 0) or 0) for p in balance.get("positions", []))
    today = today or date.today().isoformat()

    rows = [r for r in load_nav(market) if r["date"] != today]
    fp = _flow_path(market)
    flow = sum(x["amount"] for x in json.loads(fp.read_text(encoding="utf-8"))) if fp.exists() else 0.0

    if rows and rows[-1]["total"] > 0:
        unit = rows[-1]["unit_nav"] * (total - flow) / rows[-1]["total"]
    else:
        unit = 1.0
    row = {"date": today, "total": round(total, 2), "cash": round(cash, 2),
           "flow": round(flow, 2), "unit_nav": round(unit, 6)}
    rows.append(row)

    LIVE_DIR.mkdir(exist_ok=True)
    with nav_path(market).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=NAV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    if fp.exists():
        fp.unlink()
    return row


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
    args = p.parse_args()
    if args.cashflow is not None:
        total = add_cashflow(args.market, args.cashflow)
        print(f"{args.market} 입출금 예약: {args.cashflow:+,.2f} (미반영 합계 {total:+,.2f})")
    if args.status or args.cashflow is None:
        g = RiskGuard.from_config(market=args.market)
        print(g.summary_line())
        print("check:", g.check())


if __name__ == "__main__":
    _cli()
