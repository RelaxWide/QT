"""
Phase D — 리스크 가드

체크 순서 (모든 주문 전 호출):
    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"주문 차단: {reason}")
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import yaml

LIVE_DIR    = Path("live_trading")
KILL_SWITCH = LIVE_DIR / "KILL_SWITCH"
NAV_FILE    = Path("paper_trading/daily_nav.csv")
CONFIG_PATH = Path("config_live.yaml")

log = logging.getLogger("kis")


class RiskGuard:
    def __init__(self, cfg: dict):
        rg = cfg.get("risk_guard", {})
        self.daily_loss_pct      = rg.get("daily_loss_pct", 1.5)
        self.max_drawdown_pct    = rg.get("max_drawdown_pct", 10.0)
        self.consecutive_losses  = rg.get("consecutive_losses", 5)
        self.max_order_pct       = rg.get("max_order_pct_of_capital", 50)
        self.total_capital       = cfg.get("capital", {}).get("total_usd", 10000)

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH) -> "RiskGuard":
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(cfg)

    def check(self, allow_exits: bool = False) -> tuple[bool, str]:
        """
        신규 진입 가능 여부 반환.
        allow_exits=True 시 청산은 차단하지 않음 (daily_loss 도달 시 사용).
        """
        # 1. Kill switch
        if KILL_SWITCH.exists():
            return False, "KILL_SWITCH 파일 존재 — 수동 정지"

        # 2. 연속 손실
        streak = self._consecutive_loss_streak()
        if streak >= self.consecutive_losses:
            resume = self._resume_date_after_streak()
            today  = date.today()
            if today < resume:
                return False, (f"연속 손실 {streak}회 — {resume} 까지 신규 진입 보류")

        # 3. 누적 낙폭
        dd = self._current_drawdown_pct()
        if dd <= -self.max_drawdown_pct:
            return False, f"누적 낙폭 {dd:.2f}% — 전면 정지 (한도: -{self.max_drawdown_pct}%)"

        # 4. 당일 손실 (청산은 허용)
        if not allow_exits:
            daily = self._daily_pnl_pct()
            if daily <= -self.daily_loss_pct:
                return False, f"당일 손실 {daily:.2f}% — 신규 진입 차단 (한도: -{self.daily_loss_pct}%)"

        return True, ""

    def check_order_size(self, order_usd: float) -> tuple[bool, str]:
        """단일 주문 금액 한도 체크."""
        limit = self.total_capital * self.max_order_pct / 100
        if order_usd > limit:
            return False, (f"주문 금액 ${order_usd:.0f} > 한도 ${limit:.0f} "
                           f"(자본의 {self.max_order_pct}%)")
        return True, ""

    # ── 내부 계산 ─────────────────────────────────────────────────────────
    def _load_all_trades(self) -> list[dict]:
        import csv
        trades = []
        for f in LIVE_DIR.glob("trades_live_*.csv"):
            if f.stat().st_size == 0:
                continue
            with f.open(encoding="utf-8") as fh:
                trades.extend(list(csv.DictReader(fh)))
        return trades

    def _consecutive_loss_streak(self) -> int:
        trades = self._load_all_trades()
        if not trades:
            return 0
        trades.sort(key=lambda r: r.get("exit_date", r.get("date", "")), reverse=True)
        streak = 0
        for t in trades:
            pnl = float(t.get("pnl", 0))
            if pnl < 0:
                streak += 1
            else:
                break
        return streak

    def _resume_date_after_streak(self) -> date:
        trades = self._load_all_trades()
        if not trades:
            return date.today()
        trades.sort(key=lambda r: r.get("exit_date", r.get("date", "")), reverse=True)
        last_loss_date_str = trades[0].get("exit_date", trades[0].get("date", ""))
        try:
            last_loss = date.fromisoformat(last_loss_date_str[:10])
        except Exception:
            return date.today()
        return last_loss + timedelta(weeks=1)

    def _current_drawdown_pct(self) -> float:
        """daily_nav.csv 기준 현재 낙폭 계산."""
        if not NAV_FILE.exists() or NAV_FILE.stat().st_size == 0:
            return 0.0
        try:
            import csv
            with NAV_FILE.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if not rows:
                return 0.0
            navs = [float(r["total_usd"]) for r in rows if r.get("total_usd")]
            if not navs:
                return 0.0
            peak = max(navs)
            cur  = navs[-1]
            return (cur - peak) / peak * 100
        except Exception:
            return 0.0

    def _daily_pnl_pct(self) -> float:
        """오늘 날짜 청산 거래 기준 당일 PnL%."""
        today_str = str(date.today())
        trades = self._load_all_trades()
        today_pnl = sum(
            float(t.get("pnl", 0))
            for t in trades
            if t.get("exit_date", t.get("date", ""))[:10] == today_str
        )
        if self.total_capital <= 0:
            return 0.0
        return today_pnl / self.total_capital * 100
