"""
실전 포지션 추적기 — positions_live_*.json / trades_live_*.csv / slippage_log.csv
"""
from __future__ import annotations

import csv
import json
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


def save_live_positions(strategy: str, positions: dict[str, LivePosition]) -> None:
    LIVE_DIR.mkdir(exist_ok=True)
    _pos_file(strategy).write_text(
        json.dumps({sym: asdict(pos) for sym, pos in positions.items()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_live_trade(strategy: str, record: dict) -> None:
    f = _trades_file(strategy)
    LIVE_DIR.mkdir(exist_ok=True)
    is_new = not f.exists() or f.stat().st_size == 0
    with f.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)


def append_slippage(record: dict) -> None:
    """signal_price vs fill_price 기록."""
    LIVE_DIR.mkdir(exist_ok=True)
    is_new = not SLIPPAGE_LOG.exists() or SLIPPAGE_LOG.stat().st_size == 0
    with SLIPPAGE_LOG.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)
