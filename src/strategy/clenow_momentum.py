"""
Clenow "Stocks on the Move" — 주간 상대강도 모멘텀

진입/보유 조건:
  1. SPY > MA200 (레짐) — 아래면 신규 매수 중단 (spy_regime_ok, 호출측에서 판정)
  2. 종가 > MA100 (개별주 상승추세)
  3. ATR(20) / close < atr_pct_max (과변동 제외)
  4. 최근 gap_lookback 일 안에 하루 max_gap_pct 넘게 움직인 종목 제외 (실적 쇼크·급등주)
  5. 모멘텀 스코어 = 연환산 기울기 × R² (지수회귀 90일)
  6. 상위 N종목 보유, **변동성 역가중** (size_multiplier — ATR% 가 클수록 적게 산다)

2026-11-12: 4·6 과 레짐 확인을 원래 설계(Clenow, Stocks on the Move)대로 복원.
이전 구현은 동일 금액 매수 + 급등락 필터 없음이라 가장 요동치는 종목에 같은 돈이 들어갔다.

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


REF_ATR_PCT = 0.025          # 이 변동성의 종목이 슬롯 하나를 꽉 채운다
SIZE_MULT_RANGE = (0.4, 1.5)  # 슬롯 금액 대비 매수 배율 범위


def atr_pct_at(df: pd.DataFrame, date: pd.Timestamp, period: int = 20) -> float:
    """date 기준 ATR(period) / 종가. 계산 불가면 nan."""
    sub = df.loc[:date]
    if len(sub) < period + 1:
        return float("nan")
    close, high, low = sub["close"], sub["high"], sub["low"]
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    c = close.iloc[-1]
    return float(atr / c) if c > 0 and pd.notna(atr) else float("nan")


def size_multiplier(atr_pct: float, params: dict | None = None) -> float:
    """슬롯 금액에 곱할 배율 = 기준 변동성 / 종목 변동성 (범위 제한). 변동성을 모르면 1."""
    p = params or {}
    ref = p.get("ref_atr_pct", REF_ATR_PCT)
    lo, hi = p.get("size_mult_min", SIZE_MULT_RANGE[0]), p.get("size_mult_max", SIZE_MULT_RANGE[1])
    if not atr_pct or atr_pct != atr_pct or atr_pct <= 0:
        return 1.0
    return float(min(hi, max(lo, ref / atr_pct)))


def spy_regime_ok(spy_close: pd.Series, date: pd.Timestamp, ma_period: int = 200) -> bool | None:
    """
    SPY 종가 > MA(ma_period) 인가. 판정할 수 없으면 None (호출측은 매수를 보류한다):
      - 데이터가 ma_period 개 미만
      - **date 세션의 SPY 봉이 없음** — 며칠 전 봉으로 오늘 레짐을 판정하지 않는다
        (11/9 까지의 데이터로 11/11 을 판정하면 그 사이 급락을 못 본다)
    """
    s = spy_close.loc[:date].dropna()
    if len(s) < ma_period:
        return None
    if pd.Timestamp(s.index[-1]).normalize() != pd.Timestamp(date).normalize():
        return None
    return bool(s.iloc[-1] > s.rolling(ma_period).mean().iloc[-1])


def compute_scores(
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    params: dict,
) -> dict[str, float]:
    """date 기준 각 종목의 모멘텀 스코어 반환."""
    gap_lookback = params.get("gap_lookback", 90)
    max_gap_pct  = params.get("max_gap_pct", 0.15)     # None/0 이면 필터 끔
    lookback    = params.get("reg_lookback", 90)
    ma100_p     = params.get("ma100_period", 100)
    ma50_p      = params.get("ma50_period", 50)
    atr_p       = params.get("atr_period", 20)
    atr_pct_max = params.get("atr_pct_max", 0.12)
    # min_price: 시장에 따라 다른 임계값 (US $10 / KR ₩5,000)
    min_price   = params.get("min_price", params.get("min_price_usd", 10))
    min_score   = params.get("min_score", 0.15)
    # 레짐/벤치마크 티커는 점수 계산 대상에서 제외
    index_ticker = params.get("index_ticker", "SPY")

    scores: dict[str, float] = {}

    for sym, df in price_data.items():
        if sym == index_ticker or sym in ("SPY", "^KS11", "^KS200", "^VIX", "^VKOSPI"):
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

        # 급등락 필터 — 최근 gap_lookback 일 안에 하루 max_gap_pct 넘게 움직였으면 제외
        if max_gap_pct:
            day_moves = close.pct_change().iloc[-gap_lookback:].abs()
            if day_moves.max() > max_gap_pct:
                continue

        score = _exp_reg_score(close, lookback)
        if not np.isnan(score) and score > min_score:
            scores[sym] = score

    return scores
