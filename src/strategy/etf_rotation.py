"""
Monthly Momentum ETF Rotation (SPY/EEM/TLT)

매월 말 1개월 수익률 최고 ETF 1개를 100% 보유.
모멘텀 측정: 직전 월 종가 대비 금월 말 종가 수익률.
"""
from dataclasses import dataclass

import pandas as pd


@dataclass
class RotationSignal:
    date: pd.Timestamp    # 월말 기준일
    top_asset: str        # 매수할 ETF
    returns: dict         # 각 ETF의 1개월 수익률 (참고용)


def generate_rotation_signals(
    price_data: dict[str, pd.DataFrame],
    universe: list[str],
) -> list[RotationSignal]:
    monthly: dict[str, pd.Series] = {}
    for sym in universe:
        if sym not in price_data:
            continue
        monthly[sym] = price_data[sym]["close"].resample("ME").last()

    if not monthly:
        return []

    combined = pd.DataFrame(monthly).dropna(how="all")
    mom1 = combined.pct_change(1)  # 1개월 수익률

    signals: list[RotationSignal] = []
    for i in range(1, len(combined)):
        date     = combined.index[i]
        row_mom  = mom1.iloc[i]

        valid = {sym: row_mom[sym] for sym in universe if sym in row_mom and not pd.isna(row_mom[sym])}
        if not valid:
            continue

        top_asset = max(valid, key=lambda s: valid[s])
        signals.append(RotationSignal(
            date=date,
            top_asset=top_asset,
            returns=valid,
        ))

    return signals
