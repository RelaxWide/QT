"""
KW 슈퍼퀄리티 전략 (강환국).

PyKRX 만으로 가능한 한정 버전:
  1. 시총 하위 N% 필터
  2. ROE = EPS / BPS (근사) > min_roe (적자기업 제외)
  3. ROE 백분위 (높을수록) + 120일 변동성 백분위 (낮을수록) 평균 → quality_score
  4. 상위 top_n 동일비중

(DART OpenAPI 통합 시 GP/A, 자산성장률, 신F-Score 추가 가능 — Phase A2 향후 작업)

리밸런싱: 분기 (5/16, 8/16, 11/16, 4/1)
청산: 다음 리밸런싱일에 탈락 시 매도
"""
from __future__ import annotations

import pandas as pd

from src.fetch.fundamentals_kr import derive_quality_factors_pykrx_only
from src.strategy._kw_common import (
    FundamentalSignal,
    filter_small_cap,
    rebalance_dates_kr_quarterly,
    adjust_signals_to_trading,
)


def compute_super_quality_scores(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """슈퍼퀄리티 종목별 점수. 낮을수록 좋음 (rank 합산이라 0~1)."""
    small_cap_pct     = params.get("small_cap_pct", 0.20)
    vol_lookback_days = params.get("vol_lookback_days", 120)
    min_roe           = params.get("min_roe", 0.0)

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

    qual = derive_quality_factors_pykrx_only(
        fundamentals_panel, price_data, date,
        universe=small_caps,
        vol_lookback_days=vol_lookback_days,
        min_roe=min_roe,
    )
    if qual.empty:
        return {}
    return qual["quality_score"].to_dict()


def generate_super_quality_signals(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    top_n = params.get("top_n", 20)
    rebal_months = params.get("rebalance_months", [5, 8, 11, 4])
    rebal_dom    = params.get("rebalance_dom",    [16, 16, 16, 1])

    calendar_source = price_data.get("^KS11")
    if calendar_source is None or calendar_source.empty:
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        calendar = pd.DatetimeIndex(sorted(all_dates))
    else:
        calendar = calendar_source.index

    raw = rebalance_dates_kr_quarterly(start, end, rebal_months, rebal_dom)
    rebal_dates = adjust_signals_to_trading(raw, calendar)

    signals = []
    for date in rebal_dates:
        scores = compute_super_quality_scores(fundamentals_panel, price_data, date, params)
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
            strategy="kw_super_quality",
        ))
    return signals
