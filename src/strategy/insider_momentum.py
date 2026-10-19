"""
내부자매수 + 200일선 추세필터 (미국).

출처: SEC Form4 "P"코드(공개시장 자기자본 매수, 옵션행사·증여 등 제외) 집계 후
200일 이동평균선 위에 있는 종목만 필터링하는 룰 기반 전략 — 2026-10 리서치에서
발굴. 가격·가치·모멘텀이 아니라 정보비대칭(내부자가 뭘 아는가)이 신호 원천이라
Clenow(가격모멘텀)/Weinstein(스테이지)/KW SV(가치)와 메커니즘이 겹치지 않음.

규칙:
  1. 월말 기준 최근 N개월 내부자 "P"코드 매수 금액 합산 (ticker 별)
  2. 현재가가 200일 이동평균선 위인 종목만 통과 (추세 확인 없는 내부자매수 제외)
  3. 매수금액 상위 top_n 동일비중, 월별 리밸런싱
"""
from __future__ import annotations

import pandas as pd

from src.strategy._kw_common import FundamentalSignal


def _month_end_dates(calendar: pd.DatetimeIndex, start, end) -> list[pd.Timestamp]:
    """월별 리밸런싱일 — 각 달의 마지막 거래일."""
    idx = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if idx.empty:
        return []
    s = pd.Series(idx, index=idx)
    return list(s.groupby([idx.year, idx.month]).max())


def compute_insider_scores(
    insider_df: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """종목별 점수 (내부자매수 금액, trailing lookback_months). 높을수록 좋음."""
    lookback_months = params.get("lookback_months", 3)
    ma_window        = params.get("ma_window", 200)
    min_price        = params.get("min_price", 5.0)
    min_dollar_vol   = params.get("min_dollar_vol", 10_000_000)
    vol_window       = params.get("vol_window", 21)

    window_start = date - pd.DateOffset(months=lookback_months)
    window = insider_df[(insider_df["date"] > window_start) & (insider_df["date"] <= date)]
    if window.empty:
        return {}
    by_ticker = window.groupby("ticker")["value_usd"].sum()

    scores: dict[str, float] = {}
    for ticker, value in by_ticker.items():
        if value <= 0:
            continue
        df = price_data.get(ticker)
        if df is None or df.empty:
            continue
        avail = df.index[df.index <= date]
        if len(avail) < ma_window:
            continue
        last_date = avail[-1]
        close = df.loc[last_date, "close"]
        if pd.isna(close) or close < min_price:
            continue
        # 유동성 필터 — 소형/바이오텍 등 변동성 극단 종목 배제 (원본 전략은
        # S&P500 한정이었는데 여기선 러셀 전체 유니버스라 유동성으로 근사)
        recent = df.loc[:last_date].tail(vol_window)
        avg_dollar_vol = (recent["close"] * recent["volume"]).mean()
        if pd.isna(avg_dollar_vol) or avg_dollar_vol < min_dollar_vol:
            continue
        ma = df["close"].loc[:last_date].tail(ma_window).mean()
        if pd.isna(ma) or close <= ma:
            continue   # 200일선 아래 — 추세필터 탈락
        scores[ticker] = float(value)

    return scores


def generate_insider_momentum_signals(
    insider_df: pd.DataFrame,
    price_data: dict[str, pd.DataFrame],
    params: dict,
    start,
    end,
) -> list[FundamentalSignal]:
    top_n = params.get("top_n", 10)

    calendar_source = price_data.get("SPY")
    if calendar_source is None or calendar_source.empty:
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        calendar = pd.DatetimeIndex(sorted(all_dates))
    else:
        calendar = calendar_source.index

    rebal_dates = _month_end_dates(calendar, start, end)

    signals = []
    for date in rebal_dates:
        scores = compute_insider_scores(insider_df, price_data, date, params)
        if not scores:
            continue
        sorted_syms = sorted(scores, key=lambda s: scores[s], reverse=True)[:top_n]
        if not sorted_syms:
            continue
        weight = 1.0 / len(sorted_syms)
        signals.append(FundamentalSignal(
            date=date,
            weights={s: weight for s in sorted_syms},
            scores={s: scores[s] for s in sorted_syms},
            universe_size=len(scores),
            strategy="insider_momentum",
        ))
    return signals
