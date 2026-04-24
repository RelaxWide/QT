"""
Clenow "Stocks on the Move" — 주간 상대강도 모멘텀

진입/보유 조건:
  1. SPY > MA200 (레짐)
  2. 종가 > MA100 (개별주 상승추세)
  3. ATR(20) / close < atr_pct_max (과변동 제외)
  4. 모멘텀 스코어 = 연환산 기울기 × R² (지수회귀 90일)
  5. 상위 N종목 동일비중 보유

리밸런싱: 매주 금요일 종가 기준 스코어 계산 → 다음 월요일 시가 집행
"""
import numpy as np
import pandas as pd


def _exp_reg_score(prices: pd.Series, lookback: int) -> float:
    """log(price) 선형회귀 → 연환산 기울기 × R²."""
    if len(prices) < lookback:
        return np.nan
    y = np.log(prices.values[-lookback:].astype(float))
    x = np.arange(lookback, dtype=float)
    # polyfit: y = m*x + b
    coeffs = np.polyfit(x, y, 1)
    slope  = coeffs[0]

    # R² 계산
    y_hat  = slope * x + coeffs[1]
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2     = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    annualized = slope * 252
    return float(annualized * r2)


def compute_scores(
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """date 기준 각 종목의 모멘텀 스코어 반환."""
    lookback    = params.get("reg_lookback", 90)
    ma100_p     = params.get("ma100_period", 100)
    ma50_p      = params.get("ma50_period", 50)
    atr_p       = params.get("atr_period", 20)
    atr_pct_max = params.get("atr_pct_max", 0.12)
    min_price   = params.get("min_price_usd", 10)
    min_score   = params.get("min_score", 0.15)

    scores: dict[str, float] = {}

    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        idx = df.index.searchsorted(date)
        if idx < ma100_p + lookback:
            continue

        sub   = df.iloc[: idx + 1]
        close = sub["close"]
        c     = close.iloc[-1]

        if c < min_price:
            continue

        # MA100 필터 (종가 > MA100)
        ma100 = close.rolling(ma100_p).mean().iloc[-1]
        if pd.isna(ma100) or c <= ma100:
            continue

        # MA50 > MA100 필터 (단기 강세 확인)
        ma50 = close.rolling(ma50_p).mean().iloc[-1]
        if pd.isna(ma50) or ma50 <= ma100:
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

        score = _exp_reg_score(close, lookback)
        if not np.isnan(score) and score > min_score:
            scores[sym] = score

    return scores
