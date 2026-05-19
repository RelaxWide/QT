"""
DART 강화 KW 슈퍼퀄리티 — GP/A + ROE + 변동성.

PyKRX-only 버전 (kw_super_quality.py) 대비:
  - ROE = 진짜 ROE (DART 순이익 / 자기자본). PyKRX EPS/BPS 근사보다 정확
  - GP/A = 매출총이익 / 총자산 (강환국 핵심 퀄리티 지표)
  - 자산성장률 (보너스 factor)
  - 변동성 120일 (동일)

스코어: roe_rank + gpa_rank + vol_rank (가중 가능)

리밸런싱: 분기 (5/16, 8/16, 11/16, 4/1)
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
    """DART panel parquet 로드. 없으면 빈 DataFrame."""
    p = DART_PANEL_DIR / f"{year}.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception:
        return pd.DataFrame()


def _get_lagged_dart_data(date: pd.Timestamp) -> pd.DataFrame:
    """리밸런싱 시점에 사용 가능한 가장 최근 DART 데이터.

    reporting lag 보수적 처리:
    - 5/16: 전년도 사업보고서 (3/31 마감)
    - 8/16: 전년도 사업보고서
    - 11/16: 전년도 사업보고서
    - 4/1: 2년 전 사업보고서 (당년도 사업보고서 마감 3/31 직전이라 위험)

    실전 정확성을 위해 단순화: 항상 (year - 1) 사용. 백테스트 안전.
    """
    return _load_dart_panel(date.year - 1)


def compute_super_quality_dart_scores(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """DART 강화 슈퍼퀄리티 스코어."""
    small_cap_pct     = params.get("small_cap_pct", 0.20)
    vol_lookback_days = params.get("vol_lookback_days", 120)
    min_roe           = params.get("min_roe", 0.0)
    min_gpa           = params.get("min_gpa", 0.0)
    factor_weights    = params.get("factor_weights", {"roe": 1.0, "gpa": 1.0, "vol": 1.0})

    # 시총 (PyKRX panel)
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

    # DART panel (lagged)
    dart_df = _get_lagged_dart_data(date)
    if dart_df.empty:
        return {}

    # universe 필터
    dart_df = dart_df[dart_df.index.astype(str).isin(small_caps)]
    if dart_df.empty:
        return {}

    # 필요 컬럼 추출
    if "roe" not in dart_df.columns or "gp_a" not in dart_df.columns:
        return {}

    # 적자기업 제외
    valid = dart_df[
        (dart_df["roe"] > min_roe) &
        (dart_df["gp_a"] > min_gpa) &
        (dart_df["roe"].notna()) &
        (dart_df["gp_a"].notna())
    ].copy()
    if valid.empty:
        return {}

    # 변동성 (PyKRX price_data)
    vols = {}
    for ticker in valid.index:
        px_df = price_data.get(str(ticker))
        if px_df is None or px_df.empty:
            continue
        recent = px_df[px_df.index <= date].tail(vol_lookback_days)
        if len(recent) < vol_lookback_days // 2:
            continue
        rets = recent["close"].pct_change().dropna()
        if rets.empty or rets.std() <= 0:
            continue
        vols[ticker] = float(rets.std())
    if not vols:
        return {}

    valid["vol_120d"] = pd.Series(vols)
    valid = valid.dropna(subset=["vol_120d"])
    if valid.empty:
        return {}

    # 백분위 rank (낮을수록 좋음으로 통일: 큰 값이 좋은 것은 ascending=False)
    valid["roe_rank"] = percentile_rank(valid["roe"], ascending=False)  # ROE 높을수록 좋음
    valid["gpa_rank"] = percentile_rank(valid["gp_a"], ascending=False)  # GP/A 높을수록 좋음
    valid["vol_rank"] = percentile_rank(valid["vol_120d"], ascending=True)  # 변동성 낮을수록 좋음

    w_r = factor_weights.get("roe", 1.0)
    w_g = factor_weights.get("gpa", 1.0)
    w_v = factor_weights.get("vol", 1.0)
    total_w = w_r + w_g + w_v
    valid["quality_score"] = (
        valid["roe_rank"] * w_r + valid["gpa_rank"] * w_g + valid["vol_rank"] * w_v
    ) / total_w

    return valid["quality_score"].to_dict()


def generate_super_quality_dart_signals(
    fundamentals_panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    top_n = params.get("top_n", 15)
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
        scores = compute_super_quality_dart_scores(fundamentals_panel, price_data, date, params)
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
            strategy="kw_super_quality_dart",
        ))
    return signals
