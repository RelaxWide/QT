"""
Sector ETF Rotation — 11 SPDR Select Sector ETFs

매월 말 3개월 모멘텀(수익률) 기준 상위 N개 섹터 동일비중 보유.
"""
from dataclasses import dataclass
import pandas as pd


SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]


@dataclass
class SectorSignal:
    date: pd.Timestamp
    top_assets: list[str]
    returns: dict


def generate_sector_signals(
    price_data: dict[str, pd.DataFrame],
    universe: list[str],
    lookback_months: int = 3,
    top_n: int = 3,
) -> list[SectorSignal]:
    monthly: dict[str, pd.Series] = {}
    for sym in universe:
        if sym not in price_data:
            continue
        monthly[sym] = price_data[sym]["close"].resample("ME").last()

    if not monthly:
        return []

    combined = pd.DataFrame(monthly).dropna(how="all")
    mom = combined.pct_change(lookback_months)

    signals: list[SectorSignal] = []
    for i in range(lookback_months, len(combined)):
        date    = combined.index[i]
        row_mom = mom.iloc[i]

        valid = {s: row_mom[s] for s in universe if s in row_mom and not pd.isna(row_mom[s])}
        if not valid:
            continue

        top = sorted(valid, key=lambda s: valid[s], reverse=True)[:top_n]
        signals.append(SectorSignal(date=date, top_assets=top, returns=valid))

    return signals
