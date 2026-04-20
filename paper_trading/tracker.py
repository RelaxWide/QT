"""
Paper Trading 포지션 추적기

positions.json: 현재 보유 포지션 목록
trades.csv:     청산된 거래 기록 (누적)
"""
import json
import csv
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import date

import pandas as pd


POSITIONS_FILE = Path("paper_trading/positions.json")
TRADES_FILE    = Path("paper_trading/trades.csv")
PENDING_FILE   = Path("paper_trading/pending.json")


@dataclass
class PaperPosition:
    symbol:           str
    entry_date:       str
    entry_price:      float
    stop_initial:     float
    stop_current:     float
    targets:          list
    partial_weights:  list
    targets_hit:      int
    shares_total:     float
    shares_remaining: float
    realized_pnl:     float = 0.0


@dataclass
class PendingEntry:
    symbol:          str
    signal_date:     str
    entry_price_est: float   # 신호봉 종가 (실제 진입은 다음날 시가)
    stop:            float
    r:               float
    targets:         list
    partial_weights: list


def load_positions() -> dict[str, PaperPosition]:
    if not POSITIONS_FILE.exists():
        return {}
    data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    return {sym: PaperPosition(**pos) for sym, pos in data.items()}


def save_positions(positions: dict[str, PaperPosition]) -> None:
    POSITIONS_FILE.parent.mkdir(exist_ok=True)
    POSITIONS_FILE.write_text(
        json.dumps({sym: asdict(pos) for sym, pos in positions.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_pending() -> list[PendingEntry]:
    if not PENDING_FILE.exists():
        return []
    data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    return [PendingEntry(**e) for e in data]


def save_pending(pending: list[PendingEntry]) -> None:
    PENDING_FILE.parent.mkdir(exist_ok=True)
    PENDING_FILE.write_text(
        json.dumps([asdict(e) for e in pending], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_trade(record: dict) -> None:
    TRADES_FILE.parent.mkdir(exist_ok=True)
    write_header = not TRADES_FILE.exists()
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(record)


def get_trade_summary() -> dict:
    if not TRADES_FILE.exists():
        return {"total_trades": 0}
    df = pd.read_csv(TRADES_FILE)
    if df.empty:
        return {"total_trades": 0}
    wins = df[df["r_multiple"] > 0]
    losses = df[df["r_multiple"] <= 0]
    pf_w = wins["r_multiple"].sum()
    pf_l = losses["r_multiple"].abs().sum()
    return {
        "total_trades": len(df),
        "win_rate":     round((df["r_multiple"] > 0).mean(), 4),
        "avg_r":        round(df["r_multiple"].mean(), 4),
        "profit_factor": round(pf_w / pf_l, 4) if pf_l > 0 else float("inf"),
        "total_pnl":    round(df["pnl"].sum(), 2),
    }
