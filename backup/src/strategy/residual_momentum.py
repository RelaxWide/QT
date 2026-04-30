"""
Residual Momentum (Blitz, Huij, Martens 2011)

전통적 모멘텀이 시장 팩터(SPY) 수익에 오염되는 것을 제거.
잔차 모멘텀 = 총 수익 - beta × SPY 수익 → 개별주 고유 모멘텀만 추출.

진입/보유 조건:
  1. SPY > MA200 (레짐)
  2. 종가 > MA100 (상승추세 확인)
  3. ATR(20) / close < atr_pct_max (과변동 제외)
  4. 잔차 모멘텀 스코어 > min_score (상위 N종목 매수)

스코어 계산:
  - beta_window 기간 OLS로 beta 추정
  - lookback~skip 구간 (12개월, 최근 1개월 제외) 잔차 누적 수익
"""
import numpy as np
import pandas as pd


def _residual_score(
    stock_close: pd.Series,
    spy_close:   pd.Series,
    beta_window: int,
    lookback:    int,
    skip:        int,
) -> float:
    """잔차 누적 수익률을 스코어로 반환."""
    need = beta_window + skip + 10
    if len(stock_close) < need or len(spy_close) < need:
        return np.nan

    # 일별 수익률 (최근 beta_window일)
    s_ret = stock_close.pct_change().dropna().values[-beta_window:]
    m_ret = spy_close.pct_change().dropna().values[-beta_window:]
    if len(s_ret) < 60 or len(m_ret) < 60:
        return np.nan

    # OLS 로 beta 추정
    X = np.column_stack([np.ones(len(m_ret)), m_ret])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, s_ret, rcond=None)
    except np.linalg.LinAlgError:
        return np.nan
    _, beta = coeffs

    # lookback 기간 잔차 누적 수익 (skip 제외)
    if skip > 0:
        s_window = stock_close.pct_change().dropna().values[-(lookback + skip):-skip]
        m_window = spy_close.pct_change().dropna().values[-(lookback + skip):-skip]
    else:
        s_window = stock_close.pct_change().dropna().values[-lookback:]
        m_window = spy_close.pct_change().dropna().values[-lookback:]

    if len(s_window) < lookback // 2:
        return np.nan

    residuals = s_window - beta * m_window
    score = float(np.prod(1 + residuals) - 1)
    return score


def compute_residual_scores(
    price_data: dict[str, pd.DataFrame],
    date:       pd.Timestamp,
    params:     dict,
    spy_close:  pd.Series,
) -> dict[str, float]:
    """date 기준 각 종목의 잔차 모멘텀 스코어 반환."""
    beta_window = params.get("beta_window",  252)
    lookback    = params.get("lookback",     252)
    skip        = params.get("skip",          21)
    ma100_p     = params.get("ma100_period", 100)
    atr_p       = params.get("atr_period",    20)
    atr_pct_max = params.get("atr_pct_max", 0.12)
    min_price   = params.get("min_price_usd", 10)
    min_score   = params.get("min_score",    0.05)

    need = beta_window + skip + 20
    scores: dict[str, float] = {}

    spy_sub = spy_close[spy_close.index <= date]

    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        idx = df.index.searchsorted(date)
        if idx < need:
            continue

        sub   = df.iloc[: idx + 1]
        close = sub["close"]
        c     = close.iloc[-1]

        if c < min_price:
            continue

        # MA100 필터
        ma100 = close.rolling(ma100_p).mean().iloc[-1]
        if pd.isna(ma100) or c <= ma100:
            continue

        # ATR% 필터
        high = sub["high"]
        low  = sub["low"]
        tr   = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(atr_p).mean().iloc[-1]
        if pd.isna(atr) or atr / c > atr_pct_max:
            continue

        score = _residual_score(close, spy_sub, beta_window, lookback, skip)
        if not np.isnan(score) and score > min_score:
            scores[sym] = score

    return scores
