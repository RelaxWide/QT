"""
Clenow / Weinstein 용 단순 포지션 추적기

positions_{strategy}.json: {sym: {entry_date, entry_price, shares}}
trades_{strategy}.csv:     청산 기록
"""
import json
import csv
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class SimplePosition:
    symbol:      str
    entry_date:  str
    entry_price: float
    shares:      float
    strategy:    str


def _pos_file(strategy: str) -> Path:
    return Path(f"paper_trading/positions_{strategy}.json")


def _trades_file(strategy: str) -> Path:
    return Path(f"paper_trading/trades_{strategy}.csv")


def load_simple_positions(strategy: str) -> dict[str, SimplePosition]:
    f = _pos_file(strategy)
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {sym: SimplePosition(**pos) for sym, pos in data.items()}


def save_simple_positions(strategy: str, positions: dict[str, SimplePosition]) -> None:
    f = _pos_file(strategy)
    f.parent.mkdir(exist_ok=True)
    f.write_text(
        json.dumps({sym: asdict(pos) for sym, pos in positions.items()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_simple_trade(strategy: str, record: dict) -> None:
    f = _trades_file(strategy)
    f.parent.mkdir(exist_ok=True)
    is_new = not f.exists()
    with f.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)
