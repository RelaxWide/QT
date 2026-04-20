"""
Phase 2: 박스권 + 상승 구름 상단 지지 셋업

진입 조건:
  1. 가격 > senkou_a > senkou_b  (상승 구름 위)
  2. 후행스팬: close > close[t-26]  (추세 확인, lookahead 없음)
  3. tenkan > kijun  (단기 추세)
  4. 구름 두께: (senkou_a - senkou_b) / close >= cloud_thickness_min_pct
  5. 박스권: (20봉 고저 폭) / 중간가 <= box_width_max_pct
  6. 구름 상단 터치: low <= senkou_a * (1 + tolerance)  AND  close >= senkou_a
  7. 양봉: close > open

손절: senkou_a(진입일 고정값) - stop_atr_mult * ATR20
익절: 1.5R(50% 청산 + 본전 이동) → 3.0R(30% 청산) → 전환선 트레일(잔여 20%)
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.indicators.ichimoku import ichimoku
from src.strategy.breakout_pullback import Signal


def generate_cloud_signals(symbol: str, df: pd.DataFrame, params: dict) -> list[Signal]:
    min_price       = params.get("min_price_usd", 10)
    min_vol         = params.get("min_avg_volume", 500_000)
    tenkan_p        = params["tenkan_period"]
    kijun_p         = params["kijun_period"]
    senkou_b_p      = params["senkou_b_period"]
    shift           = params["chikou_offset"]
    thick_pct       = params["cloud_thickness_min_pct"] / 100
    box_pct         = params["box_width_max_pct"] / 100
    touch_tol       = params["cloud_top_touch_tolerance"]
    stop_atr_mult   = params["stop_atr_mult_below_senkou_a"]
    t1_r, t2_r      = params["partial_exit_r_multiples"]
    w1, w2          = params["partial_exit_weights"]
    min_rr          = params["min_rr_to_box_top"]

    # ── Indicators ────────────────────────────────────────────────────────
    ich     = ichimoku(df, tenkan_p, kijun_p, senkou_b_p, shift)
    atr_s   = calc_atr(df, 20)
    avg_vol = df["volume"].rolling(20).mean()

    tenkan   = ich["tenkan"]
    kijun    = ich["kijun"]
    senkou_a = ich["senkou_a"]
    senkou_b = ich["senkou_b"]

    box_high = df["high"].rolling(20).max()
    box_low  = df["low"].rolling(20).min()
    box_mid  = (box_high + box_low) / 2

    # ── Vectorized conditions ─────────────────────────────────────────────
    valid    = (df["close"] >= min_price) & (avg_vol >= min_vol)
    above    = (df["close"] > senkou_a) & (senkou_a > senkou_b)
    chikou   = df["close"] > df["close"].shift(shift)
    trend    = tenkan > kijun
    thick    = (senkou_a - senkou_b).abs() / df["close"].replace(0, np.nan) >= thick_pct
    box      = (box_high - box_low) / box_mid.replace(0, np.nan) <= box_pct
    touch    = (df["low"] <= senkou_a * (1 + touch_tol)) & (df["close"] >= senkou_a)
    bullish  = df["close"] > df["open"]

    signal_mask = valid & above & chikou & trend & thick & box & touch & bullish

    # ── Build signals ─────────────────────────────────────────────────────
    signals: list[Signal] = []
    seen: set = set()

    for sig_date in df.index[signal_mask]:
        loc = df.index.get_loc(sig_date)
        entry_loc = loc + 1
        if entry_loc >= len(df):
            continue

        entry_date = df.index[entry_loc]
        if entry_date in seen:
            continue

        entry_price = df["open"].iloc[entry_loc]
        if entry_price < min_price:
            continue

        sa      = senkou_a.iloc[loc]
        atr_val = atr_s.iloc[loc]
        if pd.isna(sa) or pd.isna(atr_val):
            continue

        stop = sa - stop_atr_mult * atr_val
        r    = entry_price - stop
        if r <= 0 or r / entry_price > 0.20:
            continue

        # R:R 필터: 박스 상단까지 최소 min_rr × R 거리 필요
        bh = box_high.iloc[loc]
        if not pd.isna(bh) and (bh - entry_price) < min_rr * r:
            continue

        signals.append(Signal(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            stop=stop,
            r=r,
            targets=[entry_price + t1_r * r, entry_price + t2_r * r],
            partial_weights=[w1, w2],
            trail_period=tenkan_p,
        ))
        seen.add(entry_date)

    return signals
