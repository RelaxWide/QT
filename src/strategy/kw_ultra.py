"""
KW 울트라 전략 (강환국, 밸류 + 퀄리티 + 모멘텀).

규칙:
  1. 시총 하위 N% (기본 30%, 슈퍼가치보다 큰 풀)
  2. Super Value score 백분위 + Super Quality score 백분위 + 12개월 모멘텀 백분위
  3. 가중 평균 (기본 0.4 / 0.3 / 0.3)
  4. 상위 top_n 동일비중

리밸런싱: 분기 (슈퍼가치/슈퍼퀄리티 와 동일)
"""
from __future__ import annotations

import pandas as pd

from src.fetch.fundamentals_kr import (
    derive_value_factors,
    derive_quality_factors_pykrx_only,
    percentile_rank,
)
from src.strategy._kw_common import (
    FundamentalSignal,
    filter_small_cap,
    rebalance_dates_kr_quarterly,
    adjust_signals_to_trading,
)


def _momentum_12m(
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    universe: list[str],
    lookback_months: int = 12,
    exclude_recent_month: bool = True,
) -> pd.Series:
    """12개월 모멘텀 (최근 1개월 제외, Jegadeesh-Titman 식). Series ticker→momentum (높을수록 좋음)."""
    lookback_days = lookback_months * 21
    skip_days     = 21 if exclude_recent_month else 0

    rows = {}
    for ticker in universe:
        df = price_data.get(ticker)
        if df is None or df.empty:
            continue
        recent = df[df.index <= date]
        if len(recent) < lookback_days + 5:
            continue
        if exclude_recent_month:
            past = recent.iloc[-lookback_days-skip_days:-skip_days]
        else:
            past = recent.tail(lookback_days)
        if past.empty:
            continue
        ret = past["close"].iloc[-1] / past["close"].iloc[0] - 1
        if pd.notna(ret):
            rows[ticker] = float(ret)
    return pd.Series(rows)


def compute_ultra_scores(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """울트라 종합 점수. 낮을수록 좋음 (백분위 합산)."""
    small_cap_pct = params.get("small_cap_pct", 0.30)
    weights = params.get("component_weights", {"value": 0.4, "quality": 0.3, "momentum": 0.3})
    momentum_lb = params.get("momentum_lookback_months", 12)
    exclude_recent = params.get("exclude_recent_month", True)

    # 시총
    mcap_dict: dict[str, float] = {}
    for ticker, df in fundamentals_panel.items():
        if date in df.index and "mcap" in df.columns:
            v = df.loc[date, "mcap"]
            if pd.notna(v) and v > 0:
                mcap_dict[ticker] = float(v)
    if not mcap_dict:
        return {}

    small_caps = filter_small_cap(pd.Series(mcap_dict), small_cap_pct)
    if not small_caps:
        return {}

    # 1) 가치 점수
    val = derive_value_factors(fundamentals_panel, date, universe=small_caps)
    # 2) 퀄리티 점수
    qual = derive_quality_factors_pykrx_only(
        fundamentals_panel, price_data, date, universe=small_caps,
    )
    # 3) 모멘텀
    mom_series = _momentum_12m(
        price_data, date, small_caps,
        lookback_months=momentum_lb, exclude_recent_month=exclude_recent,
    )
    mom_rank = percentile_rank(mom_series, ascending=False)   # 높을수록 0 에 가까움

    # 공통 인덱스 — 세 점수 다 있는 종목만
    common = set(val.index) & set(qual.index) & set(mom_rank.dropna().index)
    if not common:
        return {}
    scores = {}
    wv, wq, wm = weights["value"], weights["quality"], weights["momentum"]
    for t in common:
        v_score = val.at[t, "value_score"]
        q_score = qual.at[t, "quality_score"]
        m_score = mom_rank.at[t]
        if any(pd.isna(x) for x in (v_score, q_score, m_score)):
            continue
        scores[t] = wv * v_score + wq * q_score + wm * m_score
    return scores


def generate_ultra_signals(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    top_n = params.get("top_n", 20)
    rebal_months = params.get("rebalance_months", [5, 8, 11, 4])
    rebal_dom    = params.get("rebalance_dom",    [16, 16, 16, 1])
    use_regime   = params.get("use_regime", True)
    regime_index = params.get("regime_index", "^KS11")
    regime_ma    = params.get("regime_ma", 200)

    calendar_source = price_data.get(regime_index)
    if calendar_source is None or calendar_source.empty:
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        calendar = pd.DatetimeIndex(sorted(all_dates))
    else:
        calendar = calendar_source.index

    raw = rebalance_dates_kr_quarterly(start, end, rebal_months, rebal_dom)
    rebal_dates = adjust_signals_to_trading(raw, calendar)

    # 레짐 — KOSPI > MA200 일 때만 진입
    regime_ok_set: set[pd.Timestamp] = set()
    if use_regime and calendar_source is not None and not calendar_source.empty:
        ma = calendar_source["close"].rolling(regime_ma).mean()
        regime_ok_set = set(calendar_source.index[calendar_source["close"] > ma])

    signals = []
    for date in rebal_dates:
        if use_regime and date not in regime_ok_set:
            # 레짐 OFF — 전량 현금 (빈 신호로 추가)
            signals.append(FundamentalSignal(
                date=date, weights={}, scores={}, universe_size=0,
                strategy="kw_ultra",
            ))
            continue
        scores = compute_ultra_scores(fundamentals_panel, price_data, date, params)
        if not scores:
            continue
        sorted_syms = sorted(scores, key=lambda s: scores[s])[:top_n]
        if not sorted_syms:
            continue
        weight = 1.0 / len(sorted_syms)
        signals.append(FundamentalSignal(
            date=date,
            weights={s: weight for s in sorted_syms},
            scores={s: scores[s] for s in sorted_syms},
            universe_size=len(scores),
            strategy="kw_ultra",
        ))
    return signals
