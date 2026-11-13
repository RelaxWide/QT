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
# 실계좌 Phase 4 진입 대기. paper_trading/pending.json 은 GHA(run_daily.py) 소유라
# 로컬이 쓰면 QT_GitSync 의 pull --ff-only 가 막힌다 (2026-10 73커밋 정체 사고).
PHASE4_LIVE_PENDING = LIVE_DIR / "pending_phase4_live.json"

log = logging.getLogger("kis")


def phase4_live_enabled(cfg_live: dict, market: str = "us") -> bool:
    """Phase 4 에 실거래 자본이 배분돼 있는지. 0 이면 페이퍼 전용 (GHA 관리)."""
    cap = (cfg_live.get(market, {}) or {}).get("capital") or cfg_live.get("capital", {}) or {}
    return (float(cap.get("phase4_pct", 0) or 0) > 0
            or (not cap.get("auto_allocate") and float(cap.get("phase4_usd", 0) or 0) > 0))


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

    @property
    def unknown(self) -> bool:
        """접수 여부를 모르는 주문 (전송 후 응답 유실). ok 로 취급해 재전송을 막는다."""
        return self.order_no.startswith(UNKNOWN_ORDER_NO)


# 전송은 했는데 응답을 못 받은 주문의 order_no 자리표시. 접수됐을 수 있으므로 같은 키로 다시
# 보내지 않고 pending 으로 둔다 → 다음 장마감 처리의 reconcile 이 KIS 실잔고로 체결/미체결을 확정한다.
UNKNOWN_ORDER_NO = "UNKNOWN"


# ── OrderManager ──────────────────────────────────────────────────────────
class OrderManager:
    def __init__(self, kis: KISClient, cfg_live: dict, market: str = "us"):
        self.kis      = kis
        self.market   = market.lower()
        # 시장별 capital 섹션 우선 (kis 가 us/kr 섹션 분리되었을 때),
        # fallback 으로 최상위 capital 섹션 (기존 호환)
        market_cfg    = cfg_live.get(self.market, {}) or {}
        self.cap      = market_cfg.get("capital") or cfg_live.get("capital", {})
        self._map: dict = self._load_map()
        self._balance_cache: dict | None = None   # auto_allocate 용 잔고 캐시
        # 통화 키 (auto_allocate 시 잔고 합산 필드)
        self._cash_key = "cash_krw" if self.market == "kr" else "cash_usd"
        self._sym      = "₩" if self.market == "kr" else "$"
        # 단일 주문 금액 한도 (오류 보호) — 총자산 대비 %. 예전엔 설정만 있고 적용되지 않았다.
        rg = (market_cfg.get("risk_guard") or cfg_live.get("risk_guard") or {})
        self._max_order_pct = float(rg.get("max_order_pct_of_capital", 50) or 0)

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH, allow_prod: bool = False,
                    market: str = "us") -> "OrderManager":
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        kis = KISClient(cfg, allow_prod=allow_prod, market=market)
        return cls(kis, cfg, market=market)

    # ── 자본 배분 (auto_allocate 또는 명시값) ─────────────────────────────
    def _get_balance(self) -> dict:
        if self._balance_cache is None:
            try:
                self._balance_cache = self.kis.get_balance()
            except Exception as e:
                log.warning(f"[orders] KIS 잔고 조회 실패: {e} - auto_allocate 비활성화")
                empty = {self._cash_key: 0, "positions": [], "_failed": True}
                self._balance_cache = empty
        return self._balance_cache

    def _strategy_budget(self, strategy: str) -> float:
        """
        전략별 매수 예산.
        auto_allocate=true 면 KIS 총자산 × (1 - buffer_pct/100) × {strategy}_pct/100
        false 면 cap[{strategy}_usd] (US) 또는 cap[{strategy}_krw] (KR) 또는 cap[{strategy}_usd] 폴백.
        """
        suffix = "krw" if self.market == "kr" else "usd"

        if self.cap.get("auto_allocate"):
            bal = self._get_balance()
            if bal.get("_failed"):
                return float(self.cap.get(f"{strategy}_{suffix}", 0)
                             or self.cap.get(f"{strategy}_usd", 0))
            cash     = float(bal.get(self._cash_key, 0) or 0)
            eval_sum = sum(float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", []))
            total    = cash + eval_sum
            buffer   = float(self.cap.get("buffer_pct", 1.0)) / 100.0
            usable   = total * (1.0 - buffer)
            pct      = float(self.cap.get(f"{strategy}_pct", 0)) / 100.0
            return round(usable * pct, 2)
        return float(self.cap.get(f"{strategy}_{suffix}", 0)
                     or self.cap.get(f"{strategy}_usd", 0))

    def _strategy_max_positions(self, strategy: str) -> int:
        return int(self.cap.get(f"{strategy}_max_positions", 5))

    def _fmt_price(self, price: float) -> str:
        """통화 기호 + 시장별 표기."""
        if self.market == "kr":
            return f"{self._sym}{price:,.0f}"
        return f"{self._sym}{price:,.2f}"

    # ── order_map 관리 ────────────────────────────────────────────────────
    def _load_map(self) -> dict:
        if ORDER_MAP.exists():
            return json.loads(ORDER_MAP.read_text(encoding="utf-8"))
        return {}

    def _save_map(self) -> None:
        LIVE_DIR.mkdir(exist_ok=True)
        # 저장 직전 디스크 최신 상태와 병합 — 동시에 실행 중인 다른 프로세스
        # (예: KR 매도·매수 스크립트가 같은 09:00대에 겹쳐 실행)가 그 사이에
        # 쓴 내용을 덮어쓰지 않도록 한다. self._map 하나를 그대로 덤프하면
        # 나중에 저장한 프로세스가 먼저 저장한 프로세스의 기록을 통째로
        # 지워버린다 (2026-10 발견: KW SV 매수 11건이 order_map 에서만 소실됨,
        # positions_live 는 각자 자기 파일이라 영향 없었음).
        on_disk = self._load_map()
        on_disk.update(self._map)
        self._map = on_disk
        # 임시 파일 → 교체. 직접 덮어쓰면 쓰는 도중 꺼졌을 때 장부 전체가 깨져, 선기록한
        # "보내는 중" 기록까지 사라지고 다음 실행이 JSONDecodeError 로 멈췄다.
        from live_trading.tracker_live import atomic_write_text
        atomic_write_text(ORDER_MAP, json.dumps(self._map, indent=2, ensure_ascii=False))

    def _order_key(self, strategy: str, symbol: str, signal_date: str, side: str) -> str:
        return f"{strategy}:{symbol}:{signal_date}:{side}"

    def _already_sent(self, key: str) -> bool:
        if key in self._map:
            return True
        # 다른 프로세스가 기록했을 수 있으니 디스크 최신 상태도 확인. 찾으면 메모리에도 올린다
        # (예전엔 True 만 돌려줘서 호출측의 self._map[key] 가 KeyError 로 죽었다)
        disk = self._load_map()
        if key in disk:
            self._map[key] = disk[key]
            return True
        return False

    def _record(self, key: str, result: OrderResult) -> None:
        self._map[key] = {
            "order_no":  result.order_no,
            "qty":       result.qty,
            "price":     result.price,
            "timestamp": pd.Timestamp.now().isoformat(),
            "dry_run":   result.dry_run,
        }
        if self.market == "us":
            # applied: 이 주문이 포지션 장부에 반영됐는가. False 로 남으면 다음 실행 시작 때
            # recover_incomplete_orders 가 마저 반영한다.
            self._map[key].update(status="unknown" if result.unknown else "accepted", applied=False)
        self._save_map()

    def _mark_applied(self, key: str) -> None:
        try:
            if self.market == "us" and key in self._map:
                self._map[key]["applied"] = True
                self._save_map()
        except Exception as e:
            log.warning(f"[orders] 반영 표시 저장 실패 ({key}): {e}")   # 복구가 한 번 더 확인할 뿐 해가 없다

    # ── 수량 계산 ─────────────────────────────────────────────────────────
    def _calc_qty(self, price: float, budget: float) -> int:
        if price <= 0:
            return 0
        return max(1, int(budget // price))

    def _price_fits(self, price: float, budget: float) -> bool:
        """단일 주식 최소 1주 살 수 있는 가격인지 확인."""
        return 0 < price <= budget

    def _adjust_limit_price(self, price: float, side: str) -> float:
        """LIMIT 가격 호가단위 라운딩 (KR 의무, US 는 0.01)."""
        from src.markets.tick_size import round_buy_to_tick, round_sell_to_tick
        if side == "BUY":
            return round_buy_to_tick(price, self.market)
        return round_sell_to_tick(price, self.market)

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

        # 전송 직전에 남긴 "보내는 중" 기록이 그대로면, 앞 실행이 전송 전후 어딘가에서 끊긴 것이다
        # (접수 후 기록 저장 실패 포함). 접수됐을 수 있으므로 다시 보내지 않고 접수 불명으로 넘긴다.
        if self.market == "us" and not dry_run:
            stuck = self._recover_interrupted(strategy, symbol, signal_date, side, key)
            if stuck is not None:
                return stuck

        # 같은 키의 매도가 이미 나갔는데 포지션이 다시 open 이면, 앞 주문은 세션이 끝나 미체결로
        # 확정된 것이다 → 재주문은 새 키로 실제 전송한다. (예전엔 이전 결과만 돌려주고 아무 주문도
        # 내지 않아, 데이터 날짜가 안 바뀌는 동안에는 재매도가 조용히 사라졌다)
        # 재매도의 근거는 "포지션이 open" 이 아니라 reconcile 이 남긴 명시적 미체결 확정 기록이다
        # (LivePosition.unfilled_sell_no). 접수는 됐는데 포지션 저장만 실패해 open 에 머문 경우를
        # 미체결로 오판하면 같은 매도가 한 번 더 나간다 — 그때는 장부만 접수 대기로 바로잡는다.
        if side == "SELL" and self.market == "us" and not dry_run and self._already_sent(key):
            last, n = key, 2
            while self._already_sent(f"{key}#retry{n}"):
                last, n = f"{key}#retry{n}", n + 1
            pos = self._position(strategy, symbol)
            if pos is not None and pos.status == "open":
                prev = self._map[last]
                prev_no = str(prev.get("order_no") or "")
                if prev_no and pos.unfilled_sell_no == prev_no:
                    log.info(f"[orders] {last} 미체결 확정됨 — 재주문 (#{n})")
                    key = f"{key}#retry{n}"
                else:
                    log.error(f"[orders] {last} 접수됐으나 포지션 장부 미반영 — 장부만 접수 대기로 정정, 재전송 안 함")
                    if self._update_live_positions(strategy, symbol, signal_date, "SELL",
                                                   prev["qty"], prev["price"], prev_no,
                                                   submitted_at=prev.get("timestamp")):
                        self._mark_applied(last)
                    key = last
            else:
                key = last

        if self._already_sent(key):
            log.info(f"[orders] 중복 방지 — {key} 이미 전송됨")
            prev = self._map[key]
            return OrderResult(symbol=symbol, side=side, qty=prev["qty"],
                               price=prev["price"], order_no=prev["order_no"],
                               dry_run=prev["dry_run"])

        blocked = self._pre_send_block(strategy, symbol, side, qty, price)
        if blocked:
            log.error(f"[orders] {side} {symbol} x{qty} 보류: {blocked}")
            return OrderResult(symbol=symbol, side=side, qty=qty, price=price, order_no="", error=blocked)

        if dry_run:
            log.info(f"[DRY-RUN] {side} {symbol} x{qty} @${price:.2f} ({strategy})")
            # dry_run 은 order_map 에 기록 안 함 — 실전 매수 시 중복 방지 오작동 방지
            return OrderResult(symbol=symbol, side=side, qty=qty,
                               price=price, order_no="DRY_RUN", dry_run=True)

        # 전송 전에 주문 의도를 먼저 저장한다 (US). 접수 뒤 기록 저장이 실패하거나 프로세스가 죽어도
        # 다음 실행이 이 기록을 보고 재전송을 멈춘다. 저장 자체가 안 되면 보내지 않는다.
        local_id = f"L{pd.Timestamp.now():%Y%m%d%H%M%S%f}"
        if self.market == "us":
            try:
                self._map[key] = {"order_no": "", "status": "sending", "local_id": local_id, "qty": qty,
                                  "price": price, "timestamp": pd.Timestamp.now().isoformat(), "dry_run": False}
                self._save_map()
            except Exception as e:
                self._map.pop(key, None)
                log.error(f"[orders] {side} {symbol} 주문 기록 저장 실패 — 전송하지 않음: {e}")
                return OrderResult(symbol=symbol, side=side, qty=qty, price=price, order_no="",
                                   error=f"주문 기록 저장 실패: {e}")

        try:
            resp = self.kis.place_order(symbol, qty, side=side,
                                        order_type="LIMIT", price=price)
            order_no = resp.get("order_no", "")
            result = OrderResult(symbol=symbol, side=side, qty=qty,
                                 price=price, order_no=order_no)
        except Exception as e:
            if self.market == "us" and _is_ambiguous_send_error(e):
                # 요청이 나간 뒤 응답을 못 받았다 — 접수됐을 수 있다. 실패로 처리하면 재실행 때
                # 같은 주문이 한 번 더 나간다. 접수 불명으로 기록하고 재전송을 막는다.
                log.error(f"[orders] {side} {symbol} 접수 불명 (응답 유실): {e} — 재전송하지 않음, "
                          f"다음 장마감 처리에서 KIS 잔고로 확정")
                result = OrderResult(symbol=symbol, side=side, qty=qty, price=price,
                                     order_no=f"{UNKNOWN_ORDER_NO}:{local_id}", error=f"접수 불명: {e}")
                self.__dict__.setdefault("unknown_orders", []).append(result)
            else:
                log.error(f"[orders] {side} {symbol} 실패: {e}")
                result = OrderResult(symbol=symbol, side=side, qty=qty,
                                     price=price, order_no="", error=str(e))

        if not result.ok and self.market == "us":
            self._drop_intent(key)       # 명시적 거부·전송 전 오류 — 다시 시도할 수 있게 의도 기록 제거
        if result.ok:
            try:
                self._record(key, result)
            except Exception as e:
                # 접수는 됐는데 기록을 못 남겼다. 디스크에는 "보내는 중" 기록이 남아 있어 재전송은 막힌다.
                log.error(f"[orders] {side} {symbol} 접수됨(order_no={result.order_no}) — 기록 저장 실패: {e}")
            # positions_live_{strategy}.json 갱신 (US: 매수=pending_buy 추가 / 매도=pending_sell 표시)
            if self._update_live_positions(strategy, symbol, signal_date, side, qty, price, result.order_no):
                self._mark_applied(key)
        return result

    def _drop_intent(self, key: str) -> None:
        try:
            disk = self._load_map()
            disk.pop(key, None)
            self._map.pop(key, None)
            from live_trading.tracker_live import atomic_write_text
            atomic_write_text(ORDER_MAP, json.dumps(disk, indent=2, ensure_ascii=False))
        except Exception as e:
            # 못 지우면 "보내는 중" 으로 남아 접수 불명 취급 — 재전송이 하루 미뤄질 뿐 중복은 없다
            log.warning(f"[orders] 의도 기록 제거 실패 ({key}): {e}")

    def _recover_interrupted(self, strategy: str, symbol: str, signal_date: str, side: str,
                             key: str) -> "OrderResult | None":
        """key (또는 그 재주문 키) 중 '보내는 중' 으로 남은 기록이 있으면 접수 불명으로 확정해 돌려준다."""
        disk = self._load_map()
        for k in [key] + sorted(x for x in disk if x.startswith(key + "#retry")):
            rec = disk.get(k) or {}
            if rec.get("status") != "sending" or rec.get("order_no"):
                continue
            order_no = f"{UNKNOWN_ORDER_NO}:{rec.get('local_id') or k}"
            rec.update(order_no=order_no, status="unknown", applied=False)   # 포지션 반영 전까지 미반영
            self._map[k] = rec
            try:
                self._save_map()
            except Exception as e:
                log.error(f"[orders] 접수 불명 기록 저장 실패 ({k}): {e}")
            result = OrderResult(symbol=symbol, side=side, qty=rec["qty"], price=rec["price"],
                                 order_no=order_no, error="접수 불명: 앞 실행이 전송 도중 중단됨")
            log.error(f"[orders] {k} 앞 실행이 전송 도중 중단 — 접수 불명 처리, 재전송하지 않음")
            # 장부를 pending 으로 맞춰 다음 장마감 처리가 KIS 잔고로 확정하게 한다
            st = self._position_status(strategy, symbol)
            if (side == "SELL" and st == "open") or (side == "BUY" and st is None):
                if self._update_live_positions(strategy, symbol, signal_date, side, rec["qty"], rec["price"],
                                               order_no, submitted_at=rec.get("timestamp")):
                    self._mark_applied(k)
            else:
                self._mark_applied(k)
            self.__dict__.setdefault("unknown_orders", []).append(result)
            return result
        return None

    def _pre_send_block(self, strategy: str, symbol: str, side: str, qty: int, price: float) -> str:
        """전송 직전 안전 확인 (US). 막아야 하면 사유, 아니면 빈 문자열. 모의 실행에도 적용해 미리 보인다."""
        if self.market != "us":
            return ""
        if side == "BUY":
            # 정지 스위치는 주문마다 다시 본다 — 실행 도중(대기·여러 종목 전송 사이)에 켜도 멈추도록
            from live_trading.risk_guard import KILL_SWITCH
            if KILL_SWITCH.exists():
                return "KILL_SWITCH 파일 존재 — 수동 정지"
            pct = getattr(self, "_max_order_pct", 0) or 0
            if pct > 0:
                total = 0.0
                if self.cap.get("auto_allocate"):
                    bal = self._get_balance()
                    if not bal.get("_failed"):
                        total = float(bal.get(self._cash_key, 0) or 0) + sum(
                            float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", []))
                else:
                    total = float(self.cap.get("total_usd", 0) or 0)     # 고정 예산 모드
                if total > 0 and qty * price > total * pct / 100:
                    return (f"주문 금액 ${qty * price:,.0f} > 단일 주문 한도 ${total * pct / 100:,.0f} "
                            f"(총자산의 {pct:g}%)")
            return ""
        # SELL — 장부 수량과 KIS 실보유가 다르면 보내지 않는다 (분할·수동 거래). 장부 수량으로 팔면
        # 일부만 팔리거나(분할 후) 거부된다. 자동 보정은 하지 않는다: KIS 수량에는 전략 밖 수동 보유가
        # 섞여 있을 수 있어 그대로 팔면 남의 몫까지 청산한다. 사람이 장부를 맞춘 뒤 다음 실행에서 나간다.
        if strategy == "core":
            return ""
        bal = self._get_balance()
        if bal.get("_failed"):
            return ""                      # 확인 불가 — 매도는 막지 않는다 (청산 지연이 더 위험)
        held = sum(int(p.get("qty", 0) or 0) for p in bal.get("positions", []) if p.get("symbol") == symbol)
        if held != qty and not self._shared_symbol(strategy, symbol):
            self.__dict__.setdefault("qty_mismatch", []).append((strategy, symbol, qty, held))
            return f"수량 불일치 — 장부 {qty}주 / KIS {held}주 (수동 확인 필요)"
        return ""

    def _shared_symbol(self, strategy: str, symbol: str) -> bool:
        """다른 전략이나 코어도 이 종목을 들고 있으면 계좌 잔고로 전략별 수량을 가릴 수 없다."""
        if symbol == self.cap.get("core_symbol", "SPY") and float(self.cap.get("core_pct", 0) or 0) > 0:
            return True
        try:
            from live_trading.tracker_live import load_live_positions
            return any(symbol in load_live_positions(s) for s in ("clenow", "weinstein") if s != strategy)
        except Exception:
            return True

    @staticmethod
    def _position(strategy: str, symbol: str):
        try:
            from live_trading.tracker_live import load_live_positions
            return load_live_positions(strategy).get(symbol)
        except Exception:
            return None

    @staticmethod
    def _position_status(strategy: str, symbol: str) -> str | None:
        """positions_live 의 상태 (open | pending_buy | pending_sell). 없거나 못 읽으면 None."""
        try:
            from live_trading.tracker_live import load_live_positions
            pos = load_live_positions(strategy).get(symbol)
            return pos.status if pos else None
        except Exception:
            return None

    def _update_live_positions(self, strategy: str, symbol: str, signal_date: str,
                                side: str, qty: int, price: float, order_no: str,
                                submitted_at: str | None = None) -> bool:
        """매수/매도 주문 접수 시 positions_live_{strategy}.json 자동 갱신. 반영에 성공하면 True.

        US: 접수 ≠ 체결. 매수는 pending_buy 로 추가, 매도는 삭제하지 않고 pending_sell
        로 표시 → 다음 daily_close 의 account.reconcile_fills() 가 KIS 실잔고로 확정.
        (2026-10: 접수 즉시 삭제하던 탓에 미체결 매도 ON/ORLY/MU 가 추적에서 사라져 방치됨)
        KR: 기존 동작 유지 (접수 = 반영). KR 은 아직 reconcile 경로가 없음.
        """
        if strategy == "core":
            return True   # 코어(SPY)는 포지션 파일 없이 KIS 잔고 기준 (place_core_topup 주석)
        try:
            from live_trading.tracker_live import LivePosition, load_live_positions, save_live_positions
            positions = load_live_positions(strategy)
            # 복구로 뒤늦게 반영할 때는 원래 접수 시각을 쓴다 — 이 시각으로 주문 세션 종료와
            # 미결제 매도대금의 세션을 정하므로, 복구 시각으로 바꾸면 확정이 하루 밀리고 세션이 틀어진다.
            now_iso = submitted_at or pd.Timestamp.now().isoformat()
            pending = self.market == "us"
            if side == "BUY":
                from datetime import date as _date
                positions[symbol] = LivePosition(
                    symbol=symbol,
                    strategy=strategy,
                    entry_date=signal_date,
                    fill_date=_date.today().isoformat(),
                    signal_price=price,
                    fill_price=price,   # US 는 reconcile 시 실체결가로 교체
                    qty=qty,
                    order_no=order_no,
                    status="pending_buy" if pending else "open",
                    submitted_at=now_iso,
                )
            elif side == "SELL" and symbol in positions:
                if pending:
                    pos = positions[symbol]
                    pos.status        = "pending_sell"
                    pos.submitted_at  = now_iso
                    pos.sell_order_no = order_no
                    pos.sell_price    = price
                    pos.sell_attempts += 1
                else:
                    del positions[symbol]
            save_live_positions(strategy, positions)
            return True
        except Exception as e:
            log.error(f"[orders] positions_live 갱신 실패 ({strategy}/{symbol}): {e} — 다음 실행 시작 때 복구")
            return False

    # ── Phase 4: pending → 장개시 MOO 주문 ───────────────────────────────
    def place_phase4_entries(self, dry_run: bool = False) -> list[OrderResult]:
        """
        live_trading/pending_phase4_live.json 읽어 각 신호를 LIMIT 주문 전송.
        Phase 4 진입은 장개시 시가 근사 — 전날 종가 +1% LIMIT으로 체결 유도.
        (KIS 모의투자는 MOO 직접 미지원, LIMIT으로 대체)
        """
        from paper_trading.tracker import load_pending
        pending = load_pending(PHASE4_LIVE_PENDING)
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
        max_pos   = self._strategy_max_positions("clenow")
        total_usd = self._strategy_budget("clenow")
        # 예산 0 이어도 매도는 처리한다 (전략 종료 청산 — 예전엔 여기서 return 해 보유분이 방치됐다)
        budget_per_pos = total_usd / max_pos if total_usd > 0 else 0.0
        log.info(f"[clenow] 예산 ${total_usd:.2f} / {max_pos}종목 = ${budget_per_pos:.2f}/종목")

        today_str = str(today.date())

        # 매도
        for sym in signals.get("sell_ma100", []) + signals.get("sell_ranked", []):
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * _sell_factor("clenow", sym), 2)
            qty = _get_live_qty("clenow", sym)
            if qty <= 0:
                log.warning(f"[clenow] {sym} SELL: 보유수량 0 — 스킵 (positions_live 미동기화)")
                continue
            r = self._send("clenow", sym, today_str, "SELL", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        # 매수
        if total_usd <= 0:
            log.info("[clenow] 실거래 자본 배분 없음 — 매수 생략")
            return results
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
        max_pos   = self._strategy_max_positions("weinstein")
        total_usd = self._strategy_budget("weinstein")
        # 예산 0 이어도 매도는 처리한다 (전략 종료 청산 — 예전엔 여기서 return 해 보유분이 방치됐다)
        budget_per_pos = total_usd / max_pos if total_usd > 0 else 0.0
        log.info(f"[weinstein] 예산 ${total_usd:.2f} / {max_pos}종목 = ${budget_per_pos:.2f}/종목")

        today_str = str(today.date())

        # 매도
        for sym in signals.get("sell_ma30", []):
            df = price_data.get(sym)
            if df is None or today not in df.index:
                continue
            cur = float(df.at[today, "close"])
            price = round(cur * _sell_factor("weinstein", sym), 2)
            qty = _get_live_qty("weinstein", sym)
            if qty <= 0:
                log.warning(f"[weinstein] {sym} SELL: 보유수량 0 — 스킵")
                continue
            r = self._send("weinstein", sym, today_str, "SELL", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        # 매수
        if total_usd <= 0:
            log.info("[weinstein] 실거래 자본 배분 없음 — 매수 생략")
            return results
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
        allow_overflow: bool = True,
        size_mult: dict | None = None,
        sectors: dict | None = None,
        max_per_sector: int | None = None,
    ) -> list:
        """
        KIS 현재가를 직접 조회해 LIMIT 매수 주문.
        wednesday_morning_buy.py 에서 사용 — daily_close 가 저장한 후보를 11 AM ET에 체결.

        allow_overflow=True (기본): 종목별 예산 초과 시에도 실잔여 cash 충분하면 1주 매수.
                  → Weinstein 신호 0 등 다른 전략 미사용 자본 활용.
        size_mult: {종목: 슬롯 금액 배율} — 변동성 역가중 (Clenow). 주면 overflow 는 끈다
                  (비싼 종목 1주가 슬롯을 몇 배 넘겨 들어오면 가중이 무의미해진다).
        Clenow 는 전략 예산 대비 남은 여유(예산 − 현재 보유 평가액 − 이번에 낸 주문)도 넘지 않는다.
        sectors / max_per_sector: 주면 **매수 시점의 실제 보유**로 업종 한도를 다시 확인한다.
                  신호 단계는 매도 예정 종목을 뺀 채로 업종을 세는데, 그 매도가 미체결이면 실제 보유는
                  그대로라 한도를 넘는다. 여기서는 KIS 잔고에 남아 있는 종목(매도 접수 중 포함)과
                  매수 체결 대기분을 전부 세고, 이번에 낸 주문도 더해 가며 확인한다.
        """
        if strategy not in ("clenow", "weinstein", "kw_super_value"):
            raise ValueError(f"strategy must be clenow|weinstein|kw_super_value, got {strategy}")

        # 잔고·현금을 모르는 상태에서는 사지 않는다 (US 주간 전략). 예전엔 config 의 고정 예산으로
        # 현금 확인 없이 주문이 나갈 수 있었다. 매수는 한 주 미뤄져도 되지만 초과 매수는 되돌릴 수 없다.
        if self.market == "us" and self.cap.get("auto_allocate") and self._get_balance().get("_failed"):
            log.error(f"[{strategy}] KIS 잔고/현금 조회 실패 — 매수 생략")
            return []

        budget = self._strategy_budget(strategy)
        if budget <= 0:
            log.info(f"[{strategy}] 예산 {self._fmt_price(budget)} - 실거래 자본 배분 없음")
            return []
        max_pos        = self._strategy_max_positions(strategy)
        budget_per_pos = budget / max_pos
        log.info(f"[{strategy}] 예산 {self._fmt_price(budget)} / {max_pos}종목 = "
                 f"{self._fmt_price(budget_per_pos)}/종목")

        # 현재 실계좌 보유 — 이미 보유한 종목은 재매수하지 않는다
        from live_trading.tracker_live import load_live_positions
        try:
            held = set(load_live_positions(strategy).keys())
        except Exception as e:
            log.warning(f"[{strategy}] positions_live 로드 실패: {e} — 보유 필터 없이 진행")
            held = set()

        candidates = [s for s in symbols if s not in held]
        if strategy in ("clenow", "weinstein"):
            # 주간 전략: max_positions 는 신규+기보유 합산 총한도.
            # 슬롯 무시하고 계속 사면 현금 고갈 + 한도 초과 보유가 됨 (2026-06 8종목 사고).
            slots = max(0, max_pos - len(held))
            if slots < len(candidates):
                log.info(f"[{strategy}] 보유 {len(held)} / 한도 {max_pos} — "
                         f"신규 매수 {slots}종목으로 제한 (후보 {len(candidates)})")
            # 변동성 역가중(size_mult)일 때는 후보를 자르지 않는다 — 종목 예산이 작아 1주도 못 사는
            # 후보를 건너뛰고 다음 순위로 내려가 빈자리를 채운다 (아래 루프에서 slots 만큼만 주문).
            if not size_mult:
                candidates = candidates[:slots]
        else:
            # 분기 전략 (kw_super_value): 리밸런싱 목록 전체가 목표 포트폴리오
            slots = max_pos
            candidates = candidates[:max_pos]

        # 실잔여 cash — 예산/종목이 맞아도 결제가능 현금을 넘는 주문은 거부됨 (APBK0952)
        bal = self._get_balance()
        if bal.get("_failed"):
            remaining_cash = float("inf")   # 잔고 조회 실패 시 기존 동작 유지
        else:
            remaining_cash = float(bal.get(self._cash_key, 0) or 0)

        if size_mult:
            allow_overflow = False

        # 전략 예산 여유 — 예전 큰 슬롯으로 산 보유분이 남아 있으면 종목 수 빈자리가 있어도 예산은 찼을 수 있다
        room = float("inf")
        if strategy == "clenow" and not bal.get("_failed"):
            eval_by_sym = {p["symbol"]: float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", [])}
            invested = 0.0
            for s, p in load_live_positions(strategy).items():
                invested += eval_by_sym.get(s) or p.qty * p.fill_price     # 체결 대기분은 주문 금액으로
            room = budget - invested
            log.info(f"[{strategy}] 보유 평가 {self._fmt_price(invested)} / 예산 {self._fmt_price(budget)} "
                     f"→ 여유 {self._fmt_price(room)}")

        # 업종별 현재 보유 수 — 추적 중인 포지션 가운데 KIS 잔고에 실제로 남아 있거나 매수 체결 대기 중인 것
        sector_count: dict = {}
        if sectors and max_per_sector:
            try:
                tracked = load_live_positions(strategy)
            except Exception:
                tracked = {}
            in_kis = None if bal.get("_failed") else {p["symbol"] for p in bal.get("positions", [])
                                                      if int(p.get("qty", 0) or 0) > 0}
            for s, p in tracked.items():
                still_held = in_kis is None or s in in_kis or p.status == "pending_buy"
                sec = sectors.get(s)
                if still_held and sec:
                    sector_count[sec] = sector_count.get(sec, 0) + 1
            log.info(f"[{strategy}] 업종별 보유(매수 시점): {sector_count} / 한도 {max_per_sector}")

        results = []
        filled = 0
        for sym in candidates:
            if filled >= slots:
                break
            sec = (sectors or {}).get(sym)
            if sec and max_per_sector and sector_count.get(sec, 0) >= max_per_sector:
                log.info(f"[{strategy}] {sym} 건너뜀 — {sec} 업종 보유 {sector_count[sec]} / 한도 {max_per_sector}")
                continue
            if room <= 0:
                log.info(f"[{strategy}] 전략 예산 여유 없음 — 남은 후보 생략")
                break
            try:
                price_info = self.kis.get_price(sym)
            except Exception as e:
                log.error(f"[{strategy}] {sym} KIS 현재가 조회 실패: {e}")
                continue
            cur = float(price_info.get("last", 0) or 0)
            if cur <= 0:
                log.warning(f"[{strategy}] {sym} 현재가 0 - 스킵")
                continue
            # +0.5% LIMIT, 호가단위 라운딩 (KR 의무, US 0.01)
            raw_price = cur * 1.005
            price = self._adjust_limit_price(raw_price, "BUY")

            qty = 0
            slot_budget = budget_per_pos * float((size_mult or {}).get(sym, 1.0))
            slot_budget = min(slot_budget, room)
            if self._price_fits(price, slot_budget):
                qty = self._calc_qty(price, slot_budget)
                affordable = int(remaining_cash // price) if remaining_cash != float("inf") else qty
                if qty > affordable:
                    log.info(f"[{strategy}] {sym} 잔여 cash {self._fmt_price(remaining_cash)} 한도로 "
                             f"{qty}주 → {affordable}주 축소")
                    qty = affordable
            if qty < 1:
                if allow_overflow and price <= remaining_cash:
                    # 예산 초과지만 실잔여 cash 로 1주 매수 가능 (다른 전략 미사용 자본 활용)
                    qty = 1
                    log.info(f"[{strategy}] {sym} 가격 {self._fmt_price(price)} > "
                             f"예산/종목 {self._fmt_price(budget_per_pos)} — 잔여 cash "
                             f"{self._fmt_price(remaining_cash)} 로 1주 매수 (overflow)")
                else:
                    log.warning(f"[{strategy}] {sym} 가격 {self._fmt_price(price)} > "
                                f"종목 예산 {self._fmt_price(slot_budget)} 또는 잔여 cash 부족 - 스킵")
                    continue

            r = self._send(strategy, sym, signal_date, "BUY", qty, price, dry_run)
            results.append(r)
            if r.ok:
                filled += 1
                room -= price * qty
                if sec:
                    sector_count[sec] = sector_count.get(sec, 0) + 1
            if r.ok:
                # 모의 실행에서도 차감한다 — 안 하면 미리보기가 현금보다 많은 주문을 보여 준다
                remaining_cash -= price * qty
            if not dry_run:
                time.sleep(0.5)
        return results

    # ── 코어 보유 (SPY) ───────────────────────────────────────────────────
    # 코어는 전략이 아니라 "목표 비중까지 채워 두는 지수 보유"다. 포지션 파일을 두지 않고
    # 매번 KIS 잔고를 기준으로 판단한다 (추가 매수가 반복되므로 접수/체결 추적이 필요 없다).
    # 자동 매도는 없다 — 비중이 목표를 넘어도 팔지 않는다 (양도세·회전 방지).
    CORE_MIN_GAP_PCT = 2.0     # 목표와의 차이가 총자산의 이 비율 미만이면 주문하지 않는다

    def core_status(self) -> dict | None:
        """{symbol, target_pct, target, held, held_pct, gap, total, cash}. 코어 미설정·잔고 실패면 None."""
        sym = self.cap.get("core_symbol", "SPY")
        pct = float(self.cap.get("core_pct", 0) or 0)
        if pct <= 0:
            return None
        bal = self._get_balance()
        if bal.get("_failed"):
            return None
        cash  = float(bal.get(self._cash_key, 0) or 0)
        evals = {p["symbol"]: float(p.get("eval_amt", 0) or 0) for p in bal.get("positions", [])}
        qtys  = {p["symbol"]: int(p.get("qty", 0) or 0) for p in bal.get("positions", [])}
        total = cash + sum(evals.values())
        buffer = float(self.cap.get("buffer_pct", 1.0)) / 100.0
        target = total * (1.0 - buffer) * pct / 100.0
        held = evals.get(sym, 0.0)
        # 분할 매수: 한 번(주 1회)에 총자산의 core_max_buy_pct 까지만 산다. 0/미설정이면 한도 없음.
        max_pct = float(self.cap.get("core_max_buy_pct", 0) or 0)
        tranche = total * max_pct / 100.0 if max_pct > 0 else float("inf")
        return {"symbol": sym, "target_pct": pct, "target": target, "held": held, "qty": qtys.get(sym, 0),
                "held_pct": held / total * 100 if total > 0 else 0.0, "gap": target - held,
                "total": total, "cash": cash, "reserve": total * buffer, "tranche": tranche}

    @staticmethod
    def core_spend(st: dict, cash_committed: float = 0.0) -> float:
        """이번에 코어 매수에 쓸 금액 = min(목표까지 남은 금액, 쓸 수 있는 현금, 1회 한도)."""
        return max(0.0, min(st["gap"], st["cash"] - cash_committed - st["reserve"], st["tranche"]))

    def place_core_topup(self, signal_date: str, dry_run: bool = False,
                         cash_committed: float = 0.0) -> list:
        """
        코어(SPY) 비중이 목표에 못 미치면 남는 현금으로 채운다 (KIS 현재가 +0.5% LIMIT).
        cash_committed: 이번 실행에서 이미 다른 주문에 쓴 현금 (잔고 캐시에는 아직 안 빠져 있다).
        """
        st = self.core_status()
        if st is None:
            log.info("[core] 코어 미설정 또는 잔고 조회 실패 — 생략")
            return []
        sym = st["symbol"]
        spendable = st["cash"] - cash_committed - st["reserve"]
        spend = self.core_spend(st, cash_committed)
        cap_s = "없음" if st["tranche"] == float("inf") else self._fmt_price(st["tranche"])
        log.info(f"[core] {sym} 목표 {self._fmt_price(st['target'])} ({st['target_pct']:.0f}%) / "
                 f"보유 {self._fmt_price(st['held'])} ({st['held_pct']:.1f}%) / 쓸 수 있는 현금 "
                 f"{self._fmt_price(spendable)} / 1회 한도 {cap_s}")
        if st["gap"] < st["total"] * self.CORE_MIN_GAP_PCT / 100:
            log.info(f"[core] 목표와의 차이 {self._fmt_price(st['gap'])} — 기준 미만, 매수 없음")
            return []
        try:
            cur = float(self.kis.get_price(sym).get("last", 0) or 0)
        except Exception as e:
            log.error(f"[core] {sym} KIS 현재가 조회 실패: {e}")
            return []
        if cur <= 0:
            log.warning(f"[core] {sym} 현재가 0 — 생략")
            return []
        price = self._adjust_limit_price(cur * 1.005, "BUY")
        qty = int(spend // price)
        if qty < 1 and st["gap"] >= price and spendable >= price:
            qty = 1      # 1회 한도가 1주 값보다 작은 계좌 — 한도 때문에 영영 못 사는 것을 막는다
        if qty < 1:
            log.info(f"[core] 쓸 수 있는 금액 {self._fmt_price(spend)} < 1주 {self._fmt_price(price)} — 매수 없음")
            return []
        return [self._send("core", sym, signal_date, "BUY", qty, price, dry_run)]

    # ── KW Super Value: 분기 리밸런싱 탈락분 매도 ─────────────────────────
    def place_kw_sv_exits(
        self,
        keep_symbols: list,
        signal_date: str,
        dry_run: bool = False,
    ) -> tuple[list, list]:
        """positions_live_kw_super_value.json 보유 중 keep_symbols 에 없는 종목 전부 매도.

        분기 리밸런싱 탈락분 매도가 수동 전용이었던 것(기존 _build_core_message
        경고 문구)을 KR 도 US(clenow/weinstein)와 동일한 _send() 경로로 자동화.
        거래정지 종목은 현재가 조회가 실패하므로 자동으로 스킵되고 skipped 에 기록됨
        (예: 008500 — 정지 해제 전까지는 어떤 방법으로도 매도 불가).

        반환: (results: list[OrderResult], skipped: list[dict])
        """
        from live_trading.tracker_live import load_live_positions
        try:
            held = load_live_positions("kw_super_value")
        except Exception as e:
            log.warning(f"[kw_sv] positions_live 로드 실패: {e} — 매도 스킵")
            return [], []

        keep = set(keep_symbols)
        drop_symbols = [s for s in held if s not in keep]
        if not drop_symbols:
            log.info("[kw_sv] 매도 대상 없음 (전종목 유지)")
            return [], []

        results = []
        skipped = []
        for sym in drop_symbols:
            qty = _get_live_qty("kw_super_value", sym)
            if qty <= 0:
                continue
            try:
                price_info = self.kis.get_price(sym)
            except Exception as e:
                log.warning(f"[kw_sv] {sym} 현재가 조회 실패 — 매도 스킵 (거래정지 추정): {e}")
                skipped.append({"symbol": sym, "qty": qty, "reason": str(e)})
                continue
            cur = float(price_info.get("last", 0) or 0)
            if cur <= 0:
                log.warning(f"[kw_sv] {sym} 현재가 0 — 매도 스킵 (거래정지 추정)")
                skipped.append({"symbol": sym, "qty": qty, "reason": "현재가 0"})
                continue
            price = self._adjust_limit_price(cur * 0.995, "SELL")
            r = self._send("kw_super_value", sym, signal_date, "SELL", qty, price, dry_run)
            results.append(r)
            if not dry_run:
                time.sleep(0.5)

        return results, skipped


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


def recover_incomplete_orders() -> list[str]:
    """
    끝까지 처리되지 못한 US 주문을 주문 장부(order_map) 전체에서 찾아 포지션 장부에 반영한다.
    daily_close · wednesday_morning_buy 가 **시작할 때** 부른다 (체결 확정보다 먼저).

      - status=sending  : 전송 전후에 끊김 → 접수 불명으로 바꾸고 pending 으로 반영
      - applied=False   : 접수·기록은 됐는데 포지션 저장이 실패 → pending 으로 반영
    반영만 하면 뒤따르는 reconcile_fills 가 KIS 실잔고로 체결/미체결을 확정한다.
    예전엔 같은 키로 다시 주문할 때만 복구돼서, 매수 후보가 만료되면 실제 보유가 장부에 영영 안 잡혔다.
    반환: 복구한 주문 설명 목록 (알림용).
    """
    if not ORDER_MAP.exists():
        return []
    from live_trading.tracker_live import atomic_write_text, load_live_positions
    disk = json.loads(ORDER_MAP.read_text(encoding="utf-8"))
    helper = object.__new__(OrderManager)        # _update_live_positions 는 market 만 쓴다
    helper.market = "us"
    out, changed = [], False
    for key, rec in disk.items():
        if not isinstance(rec, dict) or rec.get("dry_run"):
            continue
        sending = rec.get("status") == "sending" and not rec.get("order_no")
        unapplied = bool(rec.get("order_no")) and rec.get("applied") is False
        if not (sending or unapplied):
            continue
        parts = key.split("#")[0].split(":")
        if len(parts) != 4:
            continue
        strategy, symbol, signal_date, side = parts
        # 기록이 오래됐어도 닫지 않는다 — 체결된 보유는 시간이 지나도 사라지지 않는다.
        # 포지션 장부에 올려 두면 체결 확정이 KIS 잔고와 대조해 (보유 → 확정 / 없음 → 만료) 정리한다.
        if sending:
            # applied=False 를 함께 남긴다: 아래 포지션 저장이 또 실패해도 다음 실행이 다시 집는다
            rec.update(order_no=f"{UNKNOWN_ORDER_NO}:{rec.get('local_id') or key}", status="unknown",
                       applied=False)
            changed = True
        ok = True
        if strategy != "core":
            try:
                pos = load_live_positions(strategy).get(symbol)
            except Exception as e:
                log.error(f"[recover] {key} 포지션 읽기 실패: {e}")
                continue
            need = (side == "BUY" and pos is None) or (
                side == "SELL" and pos is not None and pos.status == "open"
                and pos.unfilled_sell_no != rec["order_no"])
            if need:
                ok = helper._update_live_positions(strategy, symbol, signal_date, side,
                                                   rec["qty"], rec["price"], rec["order_no"],
                                                   submitted_at=rec.get("timestamp"))
                if ok:
                    out.append(f"{side} {symbol} x{rec['qty']} ({strategy}, "
                               f"{'접수 불명' if rec.get('status') == 'unknown' else '장부 미반영'})")
            elif sending:
                out.append(f"{side} {symbol} x{rec['qty']} ({strategy}, 접수 불명)")
        elif sending:
            out.append(f"{side} {symbol} x{rec['qty']} (core, 접수 불명)")
        if ok:
            rec["applied"] = True
            changed = True
    if changed:
        atomic_write_text(ORDER_MAP, json.dumps(disk, indent=2, ensure_ascii=False))
    for line in out:
        log.error(f"[recover] 중단됐던 주문 복구: {line}")
    return out


def _is_ambiguous_send_error(e: Exception) -> bool:
    """주문 요청이 브로커에 닿았을 수도 있는 오류인가 (타임아웃·연결 끊김·5xx).
    KIS 가 명시적으로 거부한 경우(rt_cd != 0, 4xx)는 여기 오지 않거나 False."""
    try:
        import requests
        from live_trading.kis_client import OrderOutcomeUnknown
        if isinstance(e, OrderOutcomeUnknown):
            return True
        if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        # 응답 본문을 못 읽었다 — 요청은 이미 나갔다 (requests 의 JSONDecodeError 는 ValueError 계열)
        if isinstance(e, (json.JSONDecodeError, requests.exceptions.JSONDecodeError,
                          requests.exceptions.ContentDecodingError, requests.exceptions.ChunkedEncodingError)):
            return True
        if isinstance(e, requests.exceptions.HTTPError):
            code = getattr(getattr(e, "response", None), "status_code", 0) or 0
            return code >= 500
    except Exception:
        pass
    return False


# 첫 매도는 종가 -0.5% LIMIT. 이미 한 번 미체결된 종목은 -3% 로 낮춰 체결을 우선한다.
# 매도 LIMIT 은 "이 가격 이상이면 판다"라 시장가가 더 높으면 시장가에 체결되므로,
# 낮춘다고 그만큼 손해 보는 게 아니라 갭하락 시에도 빠져나올 확률만 올라간다.
SELL_FACTOR_FIRST = 0.995
SELL_FACTOR_RETRY = 0.97


def _sell_factor(strategy: str, symbol: str) -> float:
    path = LIVE_DIR / f"positions_live_{strategy}.json"
    if not path.exists():
        return SELL_FACTOR_FIRST
    pos = json.loads(path.read_text(encoding="utf-8")).get(symbol, {})
    return SELL_FACTOR_RETRY if int(pos.get("sell_attempts", 0)) > 0 else SELL_FACTOR_FIRST


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
