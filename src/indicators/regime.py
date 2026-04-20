import pandas as pd
from src.fetch.prices import fetch_prices


def compute_regime(
    start: str,
    end: str | None = None,
    ma_short: int = 50,
    ma_long: int = 200,
    vix_threshold: float = 30.0,
) -> pd.DataFrame:
    spy = fetch_prices("SPY", start, end)
    cl = spy["close"]

    regime = pd.DataFrame(index=spy.index)
    regime["spy_above_50ma"] = cl > cl.rolling(ma_short).mean()
    regime["spy_above_200ma"] = cl > cl.rolling(ma_long).mean()

    try:
        vix = fetch_prices("^VIX", start, end)
        vix_series = vix["close"].reindex(spy.index, method="ffill")
    except Exception:
        # VIX unavailable — skip filter
        vix_series = pd.Series(0.0, index=spy.index)

    regime["vix"] = vix_series
    regime["trade_ok"] = regime["spy_above_50ma"] & (vix_series <= vix_threshold)
    regime["size_factor"] = regime["spy_above_200ma"].map({True: 1.0, False: 0.5})
    return regime
