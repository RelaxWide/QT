"""
신호 → KIS 주문 변환기

사용:
    from live_trading.orders import OrderManager
    om = OrderManager.from_config()
    om.place_phase4_entries(dry_run=True)
    om.place_clenow_orders(signals, price_data, today, dry_run=True)
    om.place_weinstein_orders(signals, price_data, today, dry_run=True)
    om.place_exits(sell_symbols, strategy, dry_run=True)

CLI:
    python -m live_trading.orders --phase4 --dry-run
    python -m live_trading.orders --clenow --dry-run
    python -m live_trading.orders --weinstein --dry-run
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from live_trading.kis_client import KISClient

CONFIG_PATH   = Path("config_live.yaml")
PAPER_DIR     = Path("paper_trading")
LIVE_DIR      = Path("live_trading")
ORDER_MAP     = LIVE_DIR / "order_map.json"

log = logging.getLogger("kis")


# ── 주문 결과 ─────────────────────────────────────────────────────────────
@dataclass
class OrderResult:
    symbol:     str
    side:       str        # BUY | SELL
    qty:        int
    price:      float
    order_no:   str
    dry_run:    bool = False
    error:      str  = ""

    @property
    def ok(self) -> bool:
        return bool(self.order_no) or self.dry_run


# ── OrderManager ──────────────────────────────────────────────────────────
class OrderManager:
    def __init__(self, kis: KISClient, cfg_live: dict):
        self.kis      = kis
        self.cap      = cfg_live.get("capital", {})
        self._map: dict = self._load_map()

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH, allow_prod: bool = False) -> "OrderManager":
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        kis = KISClient(cfg, allow_prod=allow_prod)
        return cls(kis, cfg)

    # ── order_map 관리 ────────────────────────────────────────────────────
    def _load_map(self) -> dict:
        if ORDER_MAP.exists():
            return json.loads(ORDER_MAP.read_text(encoding="utf-8"))
        return {}

    def _save_map(self) -> None:
        LIVE_DIR.mkdir(exist_ok=True)
        ORDER_MAP.write_text(json.dumps(self._map, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def _order_key(self, strategy: str, symbol: str, signal_date: str, side: str) -> str:
        return f"{strategy}:{symbol}:{signal_date}:{side}"

    def _already_sent(self, key: str) -> bool:
        return key in self._map

    def _record(self, key: str, result: OrderResult) -> None:
        self._map[key] = {
            "order_no":  result.order_no,
            "qty":       result.qty,
            "price":     result.price,
            "timestamp": pd.Timestamp.now().isoformat(),
            "dry_run":   result.dry_run,
        }
        self._save_map()

    # ── 수량 계산 ─────────────────────────────────────────────────────────
    def _calc_qty(self, price: float, budget_usd: float) -> int:
        if price <= 0:
            return 0
        return max(1, int(budget_usd // price))

    def _price_fits(self, price: float, budget_usd: float) -> bool:
        """단일 주식 최소 1주 살 수 있는 가격인지 확인."""
        return 0 < price <= budget_usd

    # ── 주문 실행 (공통) ──────────────────────────────────────────────────
    def _send(
        self,
        strategy: str,
        symbol: str,
        signal_date: str,
        side: str,  # "BUY" | "SELL"
        qty: int,
        price: float,
        dry_run: bool,
    ) -> OrderResult:
        key = self._order_key(strategy, symbol, signal_date, side)

        if self._already_sent(key):
            log.info(f"[orders] 중복 방지 — {key} 이미 전송됨")
            prev = self._map[key]
            return OrderResult(symbol=symbol, side=side, qty=prev["qty"],
                               price=prev["price"], order_no=prev["order_no"],
                               dry_run=prev["dry_run"])

        if dry_run:
            log.info(f"[DRY-RUN] {side} {symbol} x{qty} @${price:.2f} ({strategy})")
            result = OrderResult(symbol=symbol, side=side, qty=qty,
                                 price=price, order_no="DRY_RUN", dry_run=True)
            self._record(key, result)
            return result

        try:
            resp = self.kis.place_order(symbol, qty, side=side,
                                        order_type="LIMIT", price=price)
            order_no = resp.get("order_no", "")
            result = OrderResult(symbol=symbol, side=side, qty=qty,
                                 price=price, order_no=order_no)
        except Exception as e:
            log.error(f"[orders] {side} {symbol} 실패: {e}")
            result = OrderResult(symbol=symbol, side=side, qty=qty,
                                 price=price, order_no="", error=str(e))

        if result.ok:
            self._record(key, result)
        return result

    # ── Phase 4: pending → 장개시 MOO 주문 ───────────────────────────────
    def place_phase4_entries(self, dry_run: bool = False) -> list[OrderResult]:
        """
        paper_trading/pending.json 읽어 각 신호를 LIMIT 주문 전송.
        Phase 4 진입은 장개시 시가 근사 — 전날 종가 +1% LIMIT으로 체결 유도.
        (KIS 모의투자는 MOO 직접 미지원, LIMIT으로 대체)
        """
        from paper_trading.tracker import load_pending
        pending = load_pending()
        if not pending:
            log.info("[phase4] pending 진입 신호 없음")
            return []

        phase4_cap = self.cap.get("phase4_usd", 3500)
        if phase4_cap <= 0:
            log.info(f"[phase4] phase4_usd={phase4_cap} — 실거래 자본 배분 없음 (페이퍼 전용)")
            return []

        budget_per_pos = phase4_cap / max(len(pending), 1)
        budget_per_pos = min(budget_per_pos, phase4_cap)

        results = []
        for entry in pending:
            price = round(entry.entry_price_est * 1.01, 2)  # 전날 종가 +1%
            if not self._price_fits(price, budget_per_pos):
                log.warning(f"[phase4] {entry.symbol} 가격 ${price:.2f} > 예산 ${budget_per_pos:.0f} — 스킵")
                continue
            qty = self._calc_qty(price, budget_per_pos)
            r = self._send("phase4", entry.symbol, entry.signal_date,
                           "BUY", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)
        return results

    # ── Clenow: 신호 → 장마감 직후 주문 ─────────────────────────────────
    def place_clenow_orders(
        self,
        signals: dict,
        price_data: dict,
        today: pd.Timestamp,
        dry_run: bool = False,
    ) -> list[OrderResult]:
        """
        signals = get_clenow_signals() 반환값
        buy:  현재가 +0.5% LIMIT (장마감 직후 전송 → 다음 장 초 체결 유도)
        sell: 현재가 -0.5% LIMIT
        """
        results = []
        max_pos  = self.cap.get("clenow_max_positions", 5)
        total_usd = self.cap.get("clenow_usd", 3500)
        if total_usd <= 0:
            log.info(f"[clenow] clenow_usd={total_usd} — 실거래 자본 배분 없음")
            return []
        budget_per_pos = total_usd / max_pos

        today_str = str(today.date())

        # 매도
        for sym in signals.get("sell_ma100", []) + signals.get("sell_ranked", []):
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * 0.995, 2)
            # 보유 수량은 positions 파일에서 읽어야 하지만 1주 기본값으로 처리
            # Phase C(포지션 동기화) 구현 후 실제 수량으로 교체
            qty = _get_live_qty("clenow", sym)
            if qty <= 0:
                log.warning(f"[clenow] {sym} SELL: 보유수량 0 — 스킵 (positions_live 미동기화)")
                continue
            r = self._send("clenow", sym, today_str, "SELL", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        # 매수
        buy_candidates = signals.get("buy", [])
        for sym in buy_candidates[:max_pos]:
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * 1.005, 2)
            if not self._price_fits(price, budget_per_pos):
                log.warning(f"[clenow] {sym} 가격 ${price:.2f} > 예산 ${budget_per_pos:.0f} — 스킵")
                continue
            qty = self._calc_qty(price, budget_per_pos)
            r = self._send("clenow", sym, today_str, "BUY", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        return results

    # ── Weinstein: 신호 → 장마감 직후 주문 ───────────────────────────────
    def place_weinstein_orders(
        self,
        signals: dict,
        price_data: dict,
        today: pd.Timestamp,
        dry_run: bool = False,
    ) -> list[OrderResult]:
        results = []
        max_pos  = self.cap.get("weinstein_max_positions", 4)
        total_usd = self.cap.get("weinstein_usd", 3000)
        if total_usd <= 0:
            log.info(f"[weinstein] weinstein_usd={total_usd} — 실거래 자본 배분 없음")
            return []
        budget_per_pos = total_usd / max_pos

        today_str = str(today.date())

        # 매도
        for sym in signals.get("sell_ma30", []):
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * 0.995, 2)
            qty = _get_live_qty("weinstein", sym)
            if qty <= 0:
                log.warning(f"[weinstein] {sym} SELL: 보유수량 0 — 스킵")
                continue
            r = self._send("weinstein", sym, today_str, "SELL", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        # 매수
        for sym in signals.get("buy", [])[:max_pos]:
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * 1.005, 2)
            if not self._price_fits(price, budget_per_pos):
                log.warning(f"[weinstein] {sym} 가격 ${price:.2f} > 예산 ${budget_per_pos:.0f} — 스킵")
                continue
            qty = self._calc_qty(price, budget_per_pos)
            r = self._send("weinstein", sym, today_str, "BUY", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        return results

    # ── KIS 실시간가 매수 (Wed 11 AM ET 트리거 전용) ──────────────────────
    def place_buys_at_kis_price(
        self,
        strategy: str,
        symbols: list,
        signal_date: str,
        dry_run: bool = False,
    ) -> list:
        """
        KIS 현재가를 직접 조회해 LIMIT 매수 주문.
        wednesday_morning_buy.py 에서 사용 — daily_close 가 저장한 후보를 11 AM ET에 체결.
        """
        if strategy not in ("clenow", "weinstein"):
            raise ValueError(f"strategy must be clenow|weinstein, got {strategy}")

        cap_key = f"{strategy}_usd"
        max_key = f"{strategy}_max_positions"
        total_usd = self.cap.get(cap_key, 0)
        if total_usd <= 0:
            log.info(f"[{strategy}] {cap_key}={total_usd} — 실거래 자본 배분 없음")
            return []
        max_pos        = self.cap.get(max_key, 5)
        budget_per_pos = total_usd / max_pos

        results = []
        for sym in symbols[:max_pos]:
            try:
                price_info = self.kis.get_price(sym)
            except Exception as e:
                log.error(f"[{strategy}] {sym} KIS 현재가 조회 실패: {e}")
                continue
            cur = float(price_info.get("last", 0) or 0)
            if cur <= 0:
                log.warning(f"[{strategy}] {sym} 현재가 0 — 스킵")
                continue
            price = round(cur * 1.005, 2)
            if not self._price_fits(price, budget_per_pos):
                log.warning(f"[{strategy}] {sym} 가격 ${price:.2f} > 예산 ${budget_per_pos:.0f} — 스킵")
                continue
            qty = self._calc_qty(price, budget_per_pos)
            r   = self._send(strategy, sym, signal_date, "BUY", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)
        return results


# ── 보유수량 조회 헬퍼 (Phase C 전 임시) ─────────────────────────────────
def _get_live_qty(strategy: str, symbol: str) -> int:
    """
    live_trading/positions_live_{strategy}.json 에서 보유수량 반환.
    Phase C 완성 전까지는 파일이 없으면 0 반환 (SELL 스킵).
    """
    path = LIVE_DIR / f"positions_live_{strategy}.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    pos = data.get(symbol, {})
    return int(pos.get("qty", 0))


# ── CLI ───────────────────────────────────────────────────────────────────
def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--phase4",    action="store_true", help="Phase 4 pending → 주문")
    p.add_argument("--clenow",    action="store_true", help="Clenow 신호 → 주문")
    p.add_argument("--weinstein", action="store_true", help="Weinstein 신호 → 주문")
    p.add_argument("--dry-run",   action="store_true", help="실제 주문 없이 시뮬레이션")
    args = p.parse_args()

    om = OrderManager.from_config()
    dry = args.dry_run

    if args.phase4:
        results = om.place_phase4_entries(dry_run=dry)
        print(f"\n[Phase 4] 주문 {len(results)}건")
        for r in results:
            status = "OK" if r.ok else "FAIL"
            print(f"  [{status}] {r.side} {r.symbol} x{r.qty} @${r.price:.2f}  order_no={r.order_no}")

    if args.clenow or args.weinstein:
        print("Clenow/Weinstein 신호 생성을 위해 가격 데이터 로딩 중...")
        import yaml as _yaml
        from src.fetch.universe import get_sp500_tickers
        from src.fetch.prices import fetch_all
        from paper_trading.live_signals import get_clenow_signals, get_weinstein_signals
        from paper_trading.simple_tracker import load_simple_positions

        cfg = _yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        today = pd.Timestamp.now().normalize()
        tickers = get_sp500_tickers()
        price_data = fetch_all(tickers, cfg["data"]["start_date"],
                               cfg["data"]["end_date"], min_bars=150)

        if args.clenow:
            cl_pos = load_simple_positions("clenow")
            sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today,
                                      is_wednesday=(today.weekday() == 2))
            results = om.place_clenow_orders(sigs, price_data, today, dry_run=dry)
            print(f"\n[Clenow] 주문 {len(results)}건")
            for r in results:
                status = "OK" if r.ok else "FAIL"
                print(f"  [{status}] {r.side} {r.symbol} x{r.qty} @${r.price:.2f}")

        if args.weinstein:
            w_pos = load_simple_positions("weinstein")
            sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today,
                                         is_wednesday=(today.weekday() == 2))
            results = om.place_weinstein_orders(sigs, price_data, today, dry_run=dry)
            print(f"\n[Weinstein] 주문 {len(results)}건")
            for r in results:
                status = "OK" if r.ok else "FAIL"
                print(f"  [{status}] {r.side} {r.symbol} x{r.qty} @${r.price:.2f}")


if __name__ == "__main__":
    _cli()
