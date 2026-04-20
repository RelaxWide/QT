from dataclasses import dataclass, field
import pandas as pd
from src.indicators.atr import atr as calc_atr
from src.indicators.donchian import donchian_channel
from src.indicators.swing import swing_low


@dataclass
class Signal:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    r: float
    targets: list           # 달러 기준 부분 청산 목표가 리스트
    partial_weights: list   # 각 목표 도달 시 청산 비중 (나머지는 트레일)
    trail_period: int


def generate_signals(symbol: str, df: pd.DataFrame, params: dict) -> list[Signal]:
    """
    Vectorized breakout-pullback signal generator.

    Logic:
      1. Breakout bar: close > prev N-day high (Donchian upper, shift=1)
      2. Pullback bar (within pb_max_bars after breakout):
         low <= breakout_price + pb_atr_mult * ATR  AND  close >= breakout_price  AND  bullish
      3. Entry: next bar's open
    """
    min_price   = params["min_price_usd"]
    min_vol     = params["min_avg_volume"]
    don_period  = params["donchian_period"]
    atr_period  = params["atr_period"]
    pb_atr_mult = params["pullback_atr_mult"]
    pb_max_bars = params["pullback_max_bars"]
    stop_atr    = params["stop_atr_mult"]
    target_r    = params["target_r_multiple"]
    trail_p     = params["trail_donchian_period"]

    atr_s     = calc_atr(df, atr_period)
    don_upper = df["high"].rolling(don_period).max().shift(1)
    swl_s     = swing_low(df["low"], lookback=10)
    avg_vol   = df["volume"].rolling(20).mean()

    valid         = (df["close"] >= min_price) & (avg_vol >= min_vol)
    breakout_mask = valid & (df["close"] > don_upper)

    bp_series  = don_upper.where(breakout_mask).shift(1).ffill(limit=pb_max_bars)
    atr_series = atr_s.where(breakout_mask).shift(1).ffill(limit=pb_max_bars)

    pullback_zone = bp_series + pb_atr_mult * atr_series
    pullback_mask = (
        bp_series.notna()
        & (df["low"]   <= pullback_zone)
        & (df["close"] >= bp_series)
        & (df["close"] >  df["open"])
    )

    signals: list[Signal] = []
    seen_entries: set = set()

    for sig_date in df.index[pullback_mask]:
        loc = df.index.get_loc(sig_date)
        entry_loc = loc + 1
        if entry_loc >= len(df):
            continue

        entry_date = df.index[entry_loc]
        if entry_date in seen_entries:
            continue

        entry_price = df["open"].iloc[entry_loc]
        if entry_price < min_price:
            continue

        atr_val = atr_series.iloc[loc]
        swl     = swl_s.iloc[loc]
        stop_atr_based = entry_price - stop_atr * atr_val
        stop = min(swl, stop_atr_based) if not pd.isna(swl) else stop_atr_based

        r = entry_price - stop
        if r <= 0 or r / entry_price > 0.15:
            continue

        signals.append(Signal(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            stop=stop,
            r=r,
            targets=[entry_price + target_r * r],
            partial_weights=[0.5],
            trail_period=trail_p,
        ))
        seen_entries.add(entry_date)

    return signals
