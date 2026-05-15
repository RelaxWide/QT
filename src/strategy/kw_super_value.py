"""
KW 슈퍼가치 전략 (강환국, "할 수 있다! 퀀트 투자").

규칙:
  1. 시총 하위 N% 필터 (소형주 효과)
  2. PER ∈ [min_per, max_per] AND PBR ∈ [min_pbr, ∞] (적자/극단 제외)
  3. PER 백분위 + PBR 백분위 평균 → 낮을수록 좋음 (value_score)
  4. value_score 하위 top_n 종목 동일비중 보유

리밸런싱: 분기 (5/16, 8/16, 11/16, 4/1)
청산: 다음 리밸런싱일에 탈락 시 매도 (개별 손절 없음)
"""
from __future__ import annotations

import pandas as pd

from src.fetch.fundamentals_kr import derive_value_factors
from src.strategy._kw_common import (
    FundamentalSignal,
    filter_small_cap,
    rebalance_dates_kr_quarterly,
    adjust_signals_to_trading,
)


def compute_super_value_scores(
    fundamentals_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """슈퍼가치 종목별 점수. 낮을수록 좋음 (value_score)."""
    small_cap_pct = params.get("small_cap_pct", 0.20)
    min_per       = params.get("min_per", 0.5)
    max_per       = params.get("max_per", 100.0)
    min_pbr       = params.get("min_pbr", 0.1)

    # 시점 시총
    mcap_dict: dict[str, float] = {}
    for ticker, df in fundamentals_panel.items():
        if date in df.index and "mcap" in df.columns:
            v = df.loc[date, "mcap"]
            if pd.notna(v) and v > 0:
                mcap_dict[ticker] = float(v)
    if not mcap_dict:
        return {}

    # 소형주 필터
    small_caps = filter_small_cap(pd.Series(mcap_dict), small_cap_pct)
    if not small_caps:
        return {}

    # 가치 팩터
    val = derive_value_factors(
        fundamentals_panel, date, universe=small_caps,
        min_per=min_per, max_per=max_per, min_pbr=min_pbr,
    )
    if val.empty:
        return {}

    return val["value_score"].to_dict()


def generate_super_value_signals(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    """전체 백테스트 기간에 대한 분기 리밸런싱 신호 시리즈."""
    top_n = params.get("top_n", 20)
    rebal_months = params.get("rebalance_months", [5, 8, 11, 4])
    rebal_dom    = params.get("rebalance_dom",    [16, 16, 16, 1])

    # 영업일 캘린더 (^KS11 또는 첫 종목의 인덱스 사용)
    calendar_source = price_data.get("^KS11")
    if calendar_source is None or calendar_source.empty:
        # 폴백: 종목 인덱스의 합집합
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        calendar = pd.DatetimeIndex(sorted(all_dates))
    else:
        calendar = calendar_source.index

    raw_dates = rebalance_dates_kr_quarterly(start, end, rebal_months, rebal_dom)
    rebal_dates = adjust_signals_to_trading(raw_dates, calendar)

    signals = []
    for date in rebal_dates:
        scores = compute_super_value_scores(fundamentals_panel, date, params)
        if not scores:
            continue
        sorted_syms = sorted(scores, key=lambda s: scores[s])[:top_n]
        if not sorted_syms:
            continue
        weight = 1.0 / len(sorted_syms)
        weights = {s: weight for s in sorted_syms}
        signals.append(FundamentalSignal(
            date=date,
            weights=weights,
            scores={s: scores[s] for s in sorted_syms},
            universe_size=len(scores),
            strategy="kw_super_value",
        ))
    return signals
