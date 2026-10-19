"""
소성벨마 (강환국) — 소형 + 성장 + 밸류 + 마진.

4가지 factor 결합:
  1. 소형  - 시총 하위 20% (필터)
  2. 성장  - 순이익 YoY 성장률 (DART, 높을수록 좋음)
  3. 밸류  - PER + PBR 백분위 (PyKRX, 낮을수록 좋음)
  4. 마진  - 영업이익률 = 영업이익 / 매출 (DART, 높을수록 좋음)

score = (value_score + growth_rank + margin_rank) / 3
top_n 동일비중, 분기 또는 연 1회 리밸런싱.

보고: 강환국 "할 수 있다! 퀀트 투자" — 한국 2008-2021 CAGR 44.3%
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

from src.fetch.fundamentals_kr import percentile_rank
from src.strategy._kw_common import (
    FundamentalSignal,
    filter_small_cap,
    rebalance_dates_kr_quarterly,
    adjust_signals_to_trading,
)


DART_PANEL_DIR = Path("data/raw/kr/dart_panel")


def _load_dart_panel(year: int) -> pd.DataFrame:
    p = DART_PANEL_DIR / f"{year}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def compute_soseongbelma_scores(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """소성벨마 종목별 점수. 낮을수록 좋음 (rank 합산 0~1)."""
    small_cap_pct  = params.get("small_cap_pct", 0.20)
    factor_weights = params.get("factor_weights", {"value": 1.0, "growth": 1.0, "margin": 1.0})
    min_growth     = params.get("min_growth", -1.0)
    min_margin     = params.get("min_margin", 0.0)

    # 1) 시총 하위 N% 필터 (PyKRX panel)
    mcap_dict: dict[str, float] = {}
    for ticker, df in fundamentals_panel.items():
        if date in df.index and "mcap" in df.columns:
            v = df.loc[date, "mcap"]
            if pd.notna(v) and v > 0:
                mcap_dict[ticker] = float(v)
    if not mcap_dict:
        return {}
    small_caps = set(filter_small_cap(pd.Series(mcap_dict), small_cap_pct))
    if not small_caps:
        return {}

    # 2) PyKRX 펀더멘털 — PER, PBR (밸류)
    per_pbr: dict[str, tuple[float, float]] = {}
    for ticker in small_caps:
        df = fundamentals_panel.get(ticker)
        if df is None or date not in df.index:
            continue
        per = df.loc[date].get("per")
        pbr = df.loc[date].get("pbr")
        if pd.isna(per) or pd.isna(pbr):
            continue
        if per <= 0 or pbr <= 0:
            continue
        per_pbr[ticker] = (float(per), float(pbr))
    if not per_pbr:
        return {}

    # 3) DART panel — 성장률 + 마진 (year-1 사업보고서)
    dart_curr = _load_dart_panel(date.year - 1)
    dart_prev = _load_dart_panel(date.year - 2)
    if dart_curr.empty or dart_prev.empty:
        return {}

    rows = []
    for ticker, (per, pbr) in per_pbr.items():
        ticker_str = str(ticker)
        if ticker_str not in dart_curr.index or ticker_str not in dart_prev.index:
            continue
        curr = dart_curr.loc[ticker_str]
        prev = dart_prev.loc[ticker_str]

        ni_curr = curr.get("net_income")
        ni_prev = prev.get("net_income")
        rev     = curr.get("revenue")
        op      = curr.get("operating_inc")
        if pd.isna(ni_curr) or pd.isna(ni_prev) or pd.isna(rev) or pd.isna(op):
            continue

        # 성장: 순이익 YoY (전년 흑자에서 + 성장만)
        if ni_prev <= 0 or ni_curr <= 0:
            continue
        growth = (ni_curr - ni_prev) / abs(ni_prev)
        if growth < min_growth:
            continue

        # 마진: 영업이익률
        if rev <= 0 or op <= min_margin * rev:
            continue
        margin = op / rev

        rows.append({"ticker": ticker, "per": per, "pbr": pbr,
                     "growth": growth, "margin": margin})

    if not rows:
        return {}
    df = pd.DataFrame(rows).set_index("ticker")

    # 백분위 (낮을수록 좋음으로 통일)
    df["per_rank"]    = percentile_rank(df["per"],    ascending=True)   # PER 낮을수록 좋음
    df["pbr_rank"]    = percentile_rank(df["pbr"],    ascending=True)
    df["value_score"] = (df["per_rank"] + df["pbr_rank"]) / 2
    df["growth_rank"] = percentile_rank(df["growth"], ascending=False)  # 성장률 높을수록 좋음
    df["margin_rank"] = percentile_rank(df["margin"], ascending=False)

    w_v = factor_weights.get("value", 1.0)
    w_g = factor_weights.get("growth", 1.0)
    w_m = factor_weights.get("margin", 1.0)
    total_w = w_v + w_g + w_m
    df["score"] = (df["value_score"] * w_v +
                   df["growth_rank"] * w_g +
                   df["margin_rank"] * w_m) / total_w

    return df["score"].to_dict()


def generate_soseongbelma_signals(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    top_n        = params.get("top_n", 20)
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
        scores = compute_soseongbelma_scores(fundamentals_panel, price_data, date, params)
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
            strategy="kw_soseongbelma",
        ))
    return signals
