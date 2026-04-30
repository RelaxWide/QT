"""
Antonacci Global Equity Momentum (GEM)

매월 말:
  1. SPY vs EFA 12개월 수익률 비교 (상대모멘텀)
  2. 승자의 12개월 수익률 > 0 이면 그 ETF 보유 (절대모멘텀)
  3. 아니면 AGG (채권) 보유

SPY = 미국주식, EFA = 선진국주식, AGG = 채권
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class GEMSignal:
    date: pd.Timestamp
    hold: str           # "SPY", "EFA", or "AGG"
    spy_ret: float
    efa_ret: float


def generate_gem_signals(
    price_data: dict[str, pd.DataFrame],
    lookback_months: int = 12,
    bond_proxy: str = "AGG",
) -> list[GEMSignal]:
    universe = ["SPY", "EFA"]
    monthly: dict[str, pd.Series] = {}
    for sym in universe + [bond_proxy]:
        if sym not in price_data:
            continue
        monthly[sym] = price_data[sym]["close"].resample("ME").last()

    if "SPY" not in monthly or "EFA" not in monthly:
        return []

    combined = pd.DataFrame(monthly).dropna(how="all")
    mom = combined.pct_change(lookback_months)

    signals: list[GEMSignal] = []
    for i in range(lookback_months, len(combined)):
        date     = combined.index[i]
        spy_ret  = mom.loc[date, "SPY"]  if "SPY" in mom.columns else float("nan")
        efa_ret  = mom.loc[date, "EFA"]  if "EFA" in mom.columns else float("nan")

        if pd.isna(spy_ret) or pd.isna(efa_ret):
            continue

        # 상대모멘텀: SPY vs EFA
        winner      = "SPY" if spy_ret >= efa_ret else "EFA"
        winner_ret  = spy_ret if winner == "SPY" else efa_ret

        # 절대모멘텀: 승자가 양수면 보유, 음수면 채권
        hold = winner if winner_ret > 0 else bond_proxy

        signals.append(GEMSignal(
            date=date,
            hold=hold,
            spy_ret=float(spy_ret),
            efa_ret=float(efa_ret),
        ))

    return signals
