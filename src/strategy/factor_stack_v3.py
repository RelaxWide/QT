"""
Phase 4-v3: Phase 4 with selected cloud-support refinements.

The base engine remains Phase 4:
  breakout-pullback -> bullish Ichimoku cloud -> SPY relative strength.

v3 adds only optional refinements learned from Phase 4-v2 experiments:
  - avoid entries that are too extended above the current cloud
  - optionally require a current-cloud support touch and reclaim
  - optionally override partial target structure

This module intentionally does not replace the live Phase 4 path.
"""
from __future__ import annotations

import pandas as pd

from src.indicators.ichimoku import ichimoku
from src.indicators.atr import atr as calc_atr
from src.strategy.factor_stack import generate_factor_signals
from src.strategy.breakout_pullback import Signal


def _passes_cloud_support_filter(
    sig: Signal,
    df: pd.DataFrame,
    cloud: pd.DataFrame,
    cfg: dict,
) -> bool:
    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    sa = cloud["senkou_a"].iloc[sig_idx]
    sb = cloud["senkou_b"].iloc[sig_idx]
    if pd.isna(sa) or pd.isna(sb):
        return False

    close = df["close"].iloc[sig_idx]
    low = df["low"].iloc[sig_idx]
    open_ = df["open"].iloc[sig_idx]
    prev_close = df["close"].iloc[sig_idx - 1] if sig_idx > 0 else close

    cloud_top = max(sa, sb)
    cloud_bottom = min(sa, sb)
    if close <= cloud_top:
        return False

    max_gap_pct = cfg.get("max_cloud_gap_pct")
    if max_gap_pct is not None:
        gap = (close - cloud_top) / close * 100
        if gap > float(max_gap_pct):
            return False

    min_thickness_pct = cfg.get("min_cloud_thickness_pct")
    if min_thickness_pct is not None:
        thickness = (cloud_top - cloud_bottom) / close * 100
        if thickness < float(min_thickness_pct):
            return False

    if cfg.get("require_support_touch", False):
        tol = cfg.get("support_touch_tolerance", 0.005)
        if low > cloud_top * (1 + tol):
            return False

    if cfg.get("require_bullish_reclaim", False):
        if close <= open_ and close <= prev_close:
            return False

    if cfg.get("require_tenkan_reclaim", False):
        tenkan = cloud["tenkan"].iloc[sig_idx]
        if pd.isna(tenkan) or close < tenkan:
            return False

    return True


def _passes_trend_template(sig: Signal, df: pd.DataFrame, cfg: dict) -> bool:
    if not cfg.get("use_trend_template", False):
        return True

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    short_p = int(cfg.get("trend_ma_short", 50))
    mid_p = int(cfg.get("trend_ma_mid", 150))
    long_p = int(cfg.get("trend_ma_long", 200))
    rising_lookback = int(cfg.get("trend_long_rising_lookback", 30))
    high52_min_ratio = float(cfg.get("trend_high52_min_ratio", 0.75))
    low52_min_ratio = float(cfg.get("trend_low52_min_ratio", 1.30))

    close = df["close"].iloc[sig_idx]
    ma_short = df["close"].rolling(short_p).mean()
    ma_mid = df["close"].rolling(mid_p).mean()
    ma_long = df["close"].rolling(long_p).mean()
    high52 = df["high"].rolling(252).max()
    low52 = df["low"].rolling(252).min()

    vals = [
        ma_short.iloc[sig_idx],
        ma_mid.iloc[sig_idx],
        ma_long.iloc[sig_idx],
        high52.iloc[sig_idx],
        low52.iloc[sig_idx],
    ]
    if any(pd.isna(v) for v in vals):
        return False
    if sig_idx - rising_lookback < 0 or pd.isna(ma_long.iloc[sig_idx - rising_lookback]):
        return False

    return (
        close > ma_short.iloc[sig_idx] > ma_mid.iloc[sig_idx] > ma_long.iloc[sig_idx]
        and ma_long.iloc[sig_idx] > ma_long.iloc[sig_idx - rising_lookback]
        and close >= high52.iloc[sig_idx] * high52_min_ratio
        and close >= low52.iloc[sig_idx] * low52_min_ratio
    )


def _find_recent_breakout_idx(sig_idx: int, df: pd.DataFrame, p1_params: dict) -> int | None:
    don_period = int(p1_params["donchian_period"])
    max_bars = int(p1_params["pullback_max_bars"])
    don_upper = df["high"].rolling(don_period).max().shift(1)
    start = max(0, sig_idx - max_bars)

    for idx in range(sig_idx, start - 1, -1):
        if pd.isna(don_upper.iloc[idx]):
            continue
        if df["close"].iloc[idx] > don_upper.iloc[idx]:
            return idx

    return None


def _passes_volume_filters(sig: Signal, df: pd.DataFrame, p1_params: dict, cfg: dict) -> bool:
    breakout_volume_mult = cfg.get("breakout_volume_mult")
    pullback_volume_max_ratio = cfg.get("pullback_volume_max_ratio")
    contraction_ratio = cfg.get("max_recent_atr_to_long_atr_ratio")

    if (
        breakout_volume_mult is None
        and pullback_volume_max_ratio is None
        and contraction_ratio is None
    ):
        return True

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    vol_period = int(cfg.get("volume_avg_period", 20))
    avg_vol = df["volume"].rolling(vol_period).mean()

    if pullback_volume_max_ratio is not None:
        av = avg_vol.iloc[sig_idx]
        if pd.isna(av) or df["volume"].iloc[sig_idx] > av * float(pullback_volume_max_ratio):
            return False

    if breakout_volume_mult is not None:
        breakout_idx = _find_recent_breakout_idx(sig_idx, df, p1_params)
        if breakout_idx is None:
            return False
        av = avg_vol.iloc[breakout_idx]
        if pd.isna(av) or df["volume"].iloc[breakout_idx] < av * float(breakout_volume_mult):
            return False

    if contraction_ratio is not None:
        atr_period = int(cfg.get("contraction_atr_period", 20))
        recent_period = int(cfg.get("contraction_recent_period", 10))
        long_period = int(cfg.get("contraction_long_period", 50))
        atr_pct = calc_atr(df, atr_period) / df["close"]
        recent = atr_pct.rolling(recent_period).mean().iloc[sig_idx]
        long = atr_pct.rolling(long_period).mean().iloc[sig_idx]
        if pd.isna(recent) or pd.isna(long) or long <= 0:
            return False
        if recent / long > float(contraction_ratio):
            return False

    return True


def _passes_52w_high_rank(
    sig: Signal,
    df: pd.DataFrame,
    high52_rank: pd.DataFrame | None,
    cfg: dict,
) -> bool:
    if not cfg.get("use_52w_high_rank", False):
        return True
    if high52_rank is None:
        return False

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    sig_date = df.index[sig_idx]
    try:
        rank = high52_rank.at[sig_date, sig.symbol]
    except KeyError:
        return False
    if pd.isna(rank):
        return False

    top_pct = float(cfg.get("high52_rank_top_pct", 30.0))
    return float(rank) >= 1 - top_pct / 100


def _passes_pocket_pivot(sig: Signal, df: pd.DataFrame, cfg: dict) -> bool:
    if not cfg.get("require_pocket_pivot", False):
        return True

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx <= 0:
        return False

    lookback = int(cfg.get("pocket_pivot_lookback", 10))
    ma_short_period = int(cfg.get("pocket_pivot_ma_short", 10))
    ma_long_period = int(cfg.get("pocket_pivot_ma_long", 50))
    close = df["close"].iloc[sig_idx]
    open_ = df["open"].iloc[sig_idx]
    volume = df["volume"].iloc[sig_idx]

    if close <= open_:
        return False

    start = max(0, sig_idx - lookback)
    prior = df.iloc[start:sig_idx]
    down_days = prior[prior["close"] < prior["open"]]
    if down_days.empty:
        return False
    if volume <= down_days["volume"].max():
        return False

    ma_short = df["close"].rolling(ma_short_period).mean().iloc[sig_idx]
    ma_long = df["close"].rolling(ma_long_period).mean().iloc[sig_idx]
    if pd.isna(ma_short) or pd.isna(ma_long):
        return False

    # Pocket pivots usually come up through or off the 10/50-day moving averages.
    touched_short = df["low"].iloc[sig_idx] <= ma_short * 1.01 and close >= ma_short
    reclaimed_long = df["low"].iloc[sig_idx] <= ma_long * 1.01 and close >= ma_long
    return bool(touched_short or reclaimed_long)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _adx_components(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        ((up_move > down_move) & (up_move > 0)) * up_move,
        index=df.index,
        dtype=float,
    ).fillna(0)
    minus_dm = pd.Series(
        ((down_move > up_move) & (down_move > 0)) * down_move,
        index=df.index,
        dtype=float,
    ).fillna(0)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    alpha = 1 / period
    atr_w = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def _passes_technical_quality(sig: Signal, df: pd.DataFrame, cfg: dict) -> bool:
    atr_pct_max = cfg.get("atr_pct_max")
    rsi_max = cfg.get("rsi_max")
    rsi_min = cfg.get("rsi_min")
    adx_min = cfg.get("adx_min")

    if atr_pct_max is None and rsi_max is None and rsi_min is None and adx_min is None:
        return True

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    if atr_pct_max is not None:
        atr_period = int(cfg.get("atr_pct_period", 20))
        atr_pct = calc_atr(df, atr_period).iloc[sig_idx] / df["close"].iloc[sig_idx]
        if pd.isna(atr_pct) or atr_pct > float(atr_pct_max):
            return False

    if rsi_max is not None or rsi_min is not None:
        period = int(cfg.get("rsi_period", 14))
        rsi_val = _rsi(df["close"], period).iloc[sig_idx]
        if pd.isna(rsi_val):
            return False
        if rsi_max is not None and rsi_val > float(rsi_max):
            return False
        if rsi_min is not None and rsi_val < float(rsi_min):
            return False

    if adx_min is not None:
        period = int(cfg.get("adx_period", 14))
        adx, plus_di, minus_di = _adx_components(df, period)
        adx_val = adx.iloc[sig_idx]
        if pd.isna(adx_val) or adx_val < float(adx_min):
            return False
        if cfg.get("adx_require_plus_di", True):
            pdi = plus_di.iloc[sig_idx]
            mdi = minus_di.iloc[sig_idx]
            if pd.isna(pdi) or pd.isna(mdi) or pdi <= mdi:
                return False

    return True


def _passes_rs_line_new_high(
    sig: Signal,
    df: pd.DataFrame,
    spx_series: pd.Series | None,
    cfg: dict,
    p1_params: dict | None = None,
) -> bool:
    """RS Line quality filter — three modes (select via config key):

    Mode A  rs_line_above_ma_period   : RS Line > its N-day MA (at signal bar)
    Mode B  rs_line_slope_positive_days: RS Line higher than N days ago (at signal bar)
    Mode C  (default / at_breakout)   : RS Line at/near rolling max
            rs_line_check_at_breakout=True uses the breakout bar instead of signal bar.

    Phase 4 is a pullback strategy, so checking "RS at signal bar >= 52w max" is
    structurally impossible — the stock has pulled back from the breakout.
    Use Mode A or B for signal-bar checks, or Mode C with at_breakout=True.
    """
    if not cfg.get("use_rs_line_new_high", False):
        return True
    if spx_series is None or spx_series.empty:
        return False

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    spx_aligned = spx_series.reindex(df.index).ffill()
    rs_line = df["close"] / spx_aligned

    # --- Mode A: RS Line above its own MA ----------------------------------
    rs_ma_period = cfg.get("rs_line_above_ma_period")
    if rs_ma_period is not None:
        rs_ma = rs_line.rolling(int(rs_ma_period)).mean()
        rv = rs_line.iloc[sig_idx]
        rm = rs_ma.iloc[sig_idx]
        if pd.isna(rv) or pd.isna(rm):
            return False
        return float(rv) >= float(rm)

    # --- Mode B: RS Line slope (higher than N days ago) --------------------
    slope_days = cfg.get("rs_line_slope_positive_days")
    if slope_days is not None:
        slope_days = int(slope_days)
        if sig_idx < slope_days:
            return False
        prev_val = rs_line.iloc[sig_idx - slope_days]
        curr_val = rs_line.iloc[sig_idx]
        if pd.isna(prev_val) or pd.isna(curr_val):
            return False
        return float(curr_val) > float(prev_val)

    # --- Mode C: RS Line at/near rolling max (original concept) ------------
    # Default: check at signal bar. With rs_line_check_at_breakout=True,
    # find the breakout bar and check RS Line there instead.
    if cfg.get("rs_line_check_at_breakout", False) and p1_params:
        check_idx = _find_recent_breakout_idx(sig_idx, df, p1_params)
        if check_idx is None:
            return False
    else:
        check_idx = sig_idx

    period = int(cfg.get("rs_line_period", 252))
    rs_max = rs_line.rolling(period).max()

    rs_val = rs_line.iloc[check_idx]
    rs_max_val = rs_max.iloc[check_idx]
    if pd.isna(rs_val) or pd.isna(rs_max_val) or rs_max_val <= 0:
        return False

    near_ratio = float(cfg.get("rs_line_near_high_ratio", 1.0))

    recent_days = cfg.get("rs_line_recent_high_days")
    if recent_days is not None:
        recent_days = int(recent_days)
        start = max(0, check_idx - recent_days + 1)
        was_high_recently = False
        for i in range(start, check_idx + 1):
            rv = rs_line.iloc[i]
            rm = rs_max.iloc[i]
            if pd.notna(rv) and pd.notna(rm) and rm > 0 and rv >= rm * near_ratio:
                was_high_recently = True
                break
        if not was_high_recently:
            return False
    else:
        if rs_val < rs_max_val * near_ratio:
            return False

    price_max_ratio = cfg.get("rs_line_price_max_ratio")
    if price_max_ratio is not None:
        close = df["close"].iloc[sig_idx]
        high52 = df["high"].rolling(252).max().iloc[sig_idx]
        if pd.isna(high52) or close >= high52 * float(price_max_ratio):
            return False

    return True


def _passes_adr(sig: Signal, df: pd.DataFrame, cfg: dict) -> bool:
    """Average Daily Range filter: (H-L)/C rolling mean >= adr_min."""
    adr_min = cfg.get("adr_min")
    if adr_min is None:
        return True

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    period = int(cfg.get("adr_period", 20))
    adr = ((df["high"] - df["low"]) / df["close"]).rolling(period).mean()
    val = adr.iloc[sig_idx]
    if pd.isna(val):
        return False
    return float(val) >= float(adr_min)


def _passes_calendar(sig: Signal, cfg: dict) -> bool:
    """Block entries in specified months (e.g., skip_months: [9] blocks September)."""
    skip_months = cfg.get("skip_months")
    if not skip_months:
        return True
    return sig.entry_date.month not in [int(m) for m in skip_months]


def _passes_fip(
    sig: Signal,
    df: pd.DataFrame,
    fip_rank: pd.DataFrame | None,
    cfg: dict,
) -> bool:
    """Frog-in-the-Pan hard filter: require smooth (continuous) momentum.

    fip_rank is a date×symbol DataFrame where higher rank = smoother momentum.
    fip_min_rank (0-1) sets the minimum percentile required (default 0.5 = top half).
    """
    if not cfg.get("use_fip_filter", False):
        return True
    if fip_rank is None or sig.symbol not in fip_rank.columns:
        return False

    entry_idx = df.index.get_loc(sig.entry_date)
    sig_idx = entry_idx - 1
    if sig_idx < 0:
        return False

    sig_date = df.index[sig_idx]
    if sig_date not in fip_rank.index:
        return False

    val = fip_rank.at[sig_date, sig.symbol]
    if pd.isna(val):
        return False

    min_rank = float(cfg.get("fip_min_rank", 0.5))
    return float(val) >= min_rank


def _apply_target_override(sig: Signal, cfg: dict) -> Signal:
    targets_r = cfg.get("partial_exit_r_multiples")
    weights = cfg.get("partial_exit_weights")
    if not targets_r:
        return sig

    if not weights:
        weights = [0.5] * len(targets_r)
    if len(weights) != len(targets_r):
        raise ValueError("phase4_v3 target and weight lengths must match")

    sig.targets = [sig.entry_price + float(r_mult) * sig.r for r_mult in targets_r]
    sig.partial_weights = [float(w) for w in weights]
    return sig


def generate_factor_signals_v3(
    symbol: str,
    df: pd.DataFrame,
    p1_params: dict,
    p2_params: dict,
    f_cfg: dict,
    v3_cfg: dict,
    mom_rank: pd.DataFrame,
    bbw_rank: pd.DataFrame,
    spy_mom: pd.Series,
    high52_rank: pd.DataFrame | None = None,
    spx_series: pd.Series | None = None,
    fip_rank: pd.DataFrame | None = None,
) -> list[Signal]:
    base_sigs = generate_factor_signals(
        symbol, df, p1_params, p2_params, f_cfg, mom_rank, bbw_rank, spy_mom
    )
    if not base_sigs:
        return []

    cloud = ichimoku(
        df,
        p2_params["tenkan_period"],
        p2_params["kijun_period"],
        p2_params["senkou_b_period"],
        p2_params["chikou_offset"],
    )

    out: list[Signal] = []
    for sig in base_sigs:
        if not _passes_cloud_support_filter(sig, df, cloud, v3_cfg):
            continue
        if not _passes_trend_template(sig, df, v3_cfg):
            continue
        if not _passes_volume_filters(sig, df, p1_params, v3_cfg):
            continue
        if not _passes_52w_high_rank(sig, df, high52_rank, v3_cfg):
            continue
        if not _passes_pocket_pivot(sig, df, v3_cfg):
            continue
        if not _passes_technical_quality(sig, df, v3_cfg):
            continue
        if not _passes_rs_line_new_high(sig, df, spx_series, v3_cfg, p1_params):
            continue
        if not _passes_adr(sig, df, v3_cfg):
            continue
        if not _passes_calendar(sig, v3_cfg):
            continue
        if not _passes_fip(sig, df, fip_rank, v3_cfg):
            continue
        out.append(_apply_target_override(sig, v3_cfg))

    return out


def build_cloud_trails(
    price_data: dict[str, pd.DataFrame],
    p2_params: dict,
    level: str,
) -> dict[str, pd.Series]:
    if level == "donchian":
        return {}

    trails: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        cloud = ichimoku(
            df,
            p2_params["tenkan_period"],
            p2_params["kijun_period"],
            p2_params["senkou_b_period"],
            p2_params["chikou_offset"],
        )
        if level == "top":
            trails[sym] = pd.concat([cloud["senkou_a"], cloud["senkou_b"]], axis=1).max(axis=1)
        elif level == "mid":
            trails[sym] = (cloud["senkou_a"] + cloud["senkou_b"]) / 2
        elif level == "bottom":
            trails[sym] = pd.concat([cloud["senkou_a"], cloud["senkou_b"]], axis=1).min(axis=1)
        else:
            raise ValueError(f"unsupported phase4_v3 trail_level: {level}")

    return trails
