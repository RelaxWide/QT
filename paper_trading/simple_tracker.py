"""
Clenow / Weinstein 용 단순 포지션 추적기 (US/KR 분리 지원)

US (기본): positions_{strategy}.json, trades_{strategy}.csv  ← 기존 파일 경로 호환
KR:        positions_{strategy}_kr.json, trades_{strategy}_kr.csv
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
    split_adjusted_through: str = ""   # 주식분할 보정 완료 기준일 (paper_trading/splits.py)


def _suffix(market: str) -> str:
    """US 는 기존 호환 위해 suffix 없음, KR 만 _kr."""
    return "" if market == "us" else f"_{market}"


def _pos_file(strategy: str, market: str = "us") -> Path:
    return Path(f"paper_trading/positions_{strategy}{_suffix(market)}.json")


def _trades_file(strategy: str, market: str = "us") -> Path:
    return Path(f"paper_trading/trades_{strategy}{_suffix(market)}.csv")


def load_simple_positions(strategy: str, market: str = "us") -> dict[str, SimplePosition]:
    f = _pos_file(strategy, market)
    if not f.exists():
        return {}
    data = json.loads(f.read_text(encoding="utf-8"))
    return {sym: SimplePosition(**pos) for sym, pos in data.items()}


def save_simple_positions(strategy: str, positions: dict[str, SimplePosition],
                          market: str = "us") -> None:
    f = _pos_file(strategy, market)
    f.parent.mkdir(exist_ok=True)
    f.write_text(
        json.dumps({sym: asdict(pos) for sym, pos in positions.items()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def append_simple_trade(strategy: str, record: dict, market: str = "us") -> None:
    f = _trades_file(strategy, market)
    f.parent.mkdir(exist_ok=True)
    is_new = not f.exists() or f.stat().st_size == 0
    with f.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if is_new:
            writer.writeheader()
        writer.writerow(record)


def get_simple_trade_summary(strategy: str, market: str = "us") -> dict:
    f = _trades_file(strategy, market)
    if not f.exists():
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0}
    with f.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "total_pnl": 0.0}
    total        = len(rows)
    wins         = sum(1 for r in rows if float(r["pnl"]) > 0)
    total_pnl    = sum(float(r["pnl"]) for r in rows)
    gross_profit = sum(float(r["pnl"]) for r in rows if float(r["pnl"]) > 0)
    gross_loss   = abs(sum(float(r["pnl"]) for r in rows if float(r["pnl"]) < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    return {
        "total_trades":  total,
        "win_rate":      wins / total,
        "profit_factor": round(pf, 2),
        "total_pnl":     round(total_pnl, 2),
    }
