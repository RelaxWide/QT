"""
Hybrid Asset Allocation (HAA)

Rules used here follow the public Keller/Keuning HAA description:
- Monthly rebalance.
- Compute momentum as the average of 1, 3, 6, and 12 month returns.
- If TIP momentum is positive, hold the top N offensive assets equal weight.
- Offensive picks with non-positive momentum are replaced by the best defensive asset.
- If TIP momentum is non-positive, hold the best defensive asset.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class HAASignal:
    date: pd.Timestamp
    weights: dict[str, float]
    mode: str
    scores: dict[str, float]


def haa_score(close_monthly: pd.Series, method: str = "equal") -> pd.Series:
    r1 = close_monthly.pct_change(1)
    r3 = close_monthly.pct_change(3)
    r6 = close_monthly.pct_change(6)
    r12 = close_monthly.pct_change(12)
    if method == "vaa":
        return 12 * r1 + 4 * r3 + 2 * r6 + r12
    return pd.concat([r1, r3, r6, r12], axis=1).mean(axis=1)


def generate_haa_signals(
    price_data: dict[str, pd.DataFrame],
    offensive: list[str],
    defensive: list[str],
    canary: str = "TIP",
    top_n: int = 4,
    score_method: str = "equal",
) -> list[HAASignal]:
    universe = sorted(set(offensive + defensive + [canary]))
    monthly_close = {
        sym: price_data[sym]["close"].resample("ME").last()
        for sym in universe
        if sym in price_data and "close" in price_data[sym]
    }
    if canary not in monthly_close:
        return []

    scores = {sym: haa_score(series, method=score_method) for sym, series in monthly_close.items()}
    combined_scores = pd.DataFrame(scores).dropna(how="all")

    signals: list[HAASignal] = []
    for date, row in combined_scores.iterrows():
        canary_score = row.get(canary)
        if pd.isna(canary_score):
            continue

        valid_defensive = {
            sym: float(row[sym])
            for sym in defensive
            if sym in row.index and pd.notna(row[sym])
        }
        if not valid_defensive:
            continue
        best_defensive = max(valid_defensive, key=valid_defensive.get)

        score_snapshot = {
            sym: float(value)
            for sym, value in row.items()
            if pd.notna(value)
        }

        if canary_score <= 0:
            signals.append(
                HAASignal(
                    date=date,
                    weights={best_defensive: 1.0},
                    mode="defensive",
                    scores=score_snapshot,
                )
            )
            continue

        valid_offensive = {
            sym: float(row[sym])
            for sym in offensive
            if sym in row.index and pd.notna(row[sym])
        }
        if not valid_offensive:
            continue

        ranked = sorted(valid_offensive, key=valid_offensive.get, reverse=True)[:top_n]
        picks = [
            sym if valid_offensive[sym] > 0 else best_defensive
            for sym in ranked
        ]

        slot_weight = 1.0 / len(picks)
        weights: dict[str, float] = {}
        for sym in picks:
            weights[sym] = weights.get(sym, 0.0) + slot_weight

        signals.append(
            HAASignal(
                date=date,
                weights=weights,
                mode="offensive",
                scores=score_snapshot,
            )
        )

    return signals
