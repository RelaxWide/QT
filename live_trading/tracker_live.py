"""
실전 포지션 추적기 — positions_live_*.json / trades_live_*.csv / slippage_log.csv
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

LIVE_DIR = Path("live_trading")
SLIPPAGE_LOG = LIVE_DIR / "slippage_log.csv"


@dataclass
class LivePosition:
    symbol:      str
    strategy:    str
    entry_date:  str   # 신호 날짜
    fill_date:   str   # 실제 체결 날짜
    signal_price: float  # 신호 시점 종가
    fill_price:  float   # 실제 체결가
    qty:         int
    order_no:    str
    # 주문 접수 ≠ 체결 (US). 접수 시점엔 pending_*, 다음 daily_close 의
    # account.reconcile_fills() 가 KIS 실잔고로 확정한다. 기존 파일은 기본값 "open".
    status:        str   = "open"   # pending_buy | open | pending_sell
    submitted_at:  str   = ""       # 마지막 주문 접수 시각 (ISO)
    sell_order_no: str   = ""
    sell_price:    float = 0.0      # 매도 LIMIT 가
    sell_attempts: int   = 0        # 미체결 재주문 횟수 판단용
    # reconcile 이 "세션이 끝났는데 안 팔렸다" 고 확정한 매도 주문번호. 재매도는 이 기록이 있을 때만
    # 나간다 — status 가 open 이라는 것만으로는 (포지션 저장 실패로 open 에 머문 경우와) 구별이 안 된다.
    unfilled_sell_no: str = ""


def _pos_file(strategy: str) -> Path:
    return LIVE_DIR / f"positions_live_{strategy}.json"


def _trades_file(strategy: str) -> Path:
    return LIVE_DIR / f"trades_live_{strategy}.csv"


def load_live_positions(strategy: str) -> dict[str, LivePosition]:
    f = _pos_file(strategy)
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {sym: LivePosition(**pos) for sym, pos in data.items()}


def atomic_write_text(path: Path, text: str) -> None:
    """임시 파일에 다 쓰고 교체한다 — 쓰는 도중 꺼져도 원래 파일이 깨지지 않는다 (옛 내용 아니면 새 내용)."""
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    # Windows 는 백신·동기화 프로그램이 대상 파일을 잠깐 잡고 있으면 교체가 거부된다 → 짧게 재시도
    import time
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2)


def save_live_positions(strategy: str, positions: dict[str, LivePosition]) -> None:
    atomic_write_text(_pos_file(strategy),
                      json.dumps({sym: asdict(pos) for sym, pos in positions.items()},
                                 indent=2, ensure_ascii=False))


def append_live_trade(strategy: str, record: dict) -> None:
    f = _trades_file(strategy)
    LIVE_DIR.mkdir(exist_ok=True)
    is_new = not f.exists() or f.stat().st_size == 0
    # 같은 매도 주문(종목+주문번호)은 한 번만 기록한다. 체결 확정이 거래 기록 뒤·포지션 저장 전에
    # 끊기면 다음 실행이 같은 체결을 다시 확정하는데, 그때 손익이 두 번 잡히지 않도록.
    order_no = str(record.get("order_no") or "")
    if not is_new and order_no and order_no != "UNKNOWN":   # 접수 불명도 "UNKNOWN:<로컬 ID>" 로 고유하다
        with f.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("symbol") == record.get("symbol") and str(row.get("order_no") or "") == order_no:
                    return
    with f.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)


def append_slippage(record: dict) -> None:
    """signal_price vs fill_price 기록."""
    LIVE_DIR.mkdir(exist_ok=True)
    is_new = not SLIPPAGE_LOG.exists() or SLIPPAGE_LOG.stat().st_size == 0
    # 같은 매수 주문(종목+주문번호)은 한 번만 — 체결 확정이 포지션 저장 전에 끊겨 재실행돼도 중복 안 되게
    order_no = str(record.get("order_no") or "")
    if not is_new and order_no and order_no != "UNKNOWN":
        with SLIPPAGE_LOG.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("symbol") == record.get("symbol") and str(row.get("order_no") or "") == order_no:
                    return
    with SLIPPAGE_LOG.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)
