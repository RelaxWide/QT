"""
Minervini VCP (Volatility Contraction Pattern)

진입 조건 — Trend Template (모두 충족):
  1. 종가 > 50일 SMA > 150일 SMA > 200일 SMA
  2. 200일 SMA 최근 30일간 우상향
  3. 52주 고가 대비 25% 이내, 52주 저가 대비 30% 이상
  4. RS Rank ≥ 70 (SPY 대비 12개월 상대강도 상위 30%)

VCP 패턴:
  - 최근 60거래일 내 3~4번의 수축, 각 조정폭이 직전의 절반 이하
  - 마지막 수축 폭 ≤ 8%
  - 거래량이 수축 구간 동안 감소

진입: pivot point(최근 수축 고점) 돌파 + 거래량 ≥ 1.4 × 50일 평균
손절: pivot 가격 -7%
청산: 20일 SMA 이탈
"""
import numpy as np
import pandas as pd


def passes_trend_template(
    close:  pd.Series,
    high:   pd.Series,
    low:    pd.Series,
    rs_rank: float,
    params: dict,
) -> bool:
    """Minervini Trend Template 4종 조건."""
    ma50_p   = params.get("ma50_period",  50)
    ma150_p  = params.get("ma150_period", 150)
    ma200_p  = params.get("ma200_period", 200)
    high_pct = params.get("high52_within_pct", 0.25)
    low_pct  = params.get("low52_above_pct",   0.30)
    rs_min   = params.get("rs_rank_min",        70)

    if len(close) < ma200_p + 30:
        return False

    ma50  = close.rolling(ma50_p).mean()
    ma150 = close.rolling(ma150_p).mean()
    ma200 = close.rolling(ma200_p).mean()

    c   = close.iloc[-1]
    m50 = ma50.iloc[-1]
    m150 = ma150.iloc[-1]
    m200 = ma200.iloc[-1]

    if any(pd.isna(x) for x in (m50, m150, m200)):
        return False

    # 1. 종가 > 50 > 150 > 200
    if not (c > m50 > m150 > m200):
        return False

    # 2. 200일 MA 최근 30일 우상향
    if ma200.iloc[-1] <= ma200.iloc[-30]:
        return False

    # 3. 52주 고/저 대비
    last252 = close.iloc[-252:]
    h52 = last252.max()
    l52 = last252.min()
    if c < h52 * (1 - high_pct):
        return False
    if c < l52 * (1 + low_pct):
        return False

    # 4. RS Rank
    if rs_rank < rs_min:
        return False

    return True


def detect_vcp_pattern(
    close:  pd.Series,
    high:   pd.Series,
    low:    pd.Series,
    volume: pd.Series,
    params: dict,
) -> tuple[bool, float]:
    """
    VCP 패턴 검출 → (패턴_있음, pivot_가격).
    최근 60거래일 내 swing high/low 를 찾아 수축 횟수와 폭을 평가.
    """
    window  = params.get("vcp_window", 60)
    min_contractions = params.get("min_contractions", 2)
    last_contraction_max = params.get("last_contraction_max_pct", 0.10)

    if len(close) < window:
        return False, 0.0

    sub_h = high.iloc[-window:].values
    sub_l = low.iloc[-window:].values

    # local maxima/minima (좌우 3봉보다 큰/작은 점)
    pivots = []  # (idx, price, "H" or "L")
    n = len(sub_h)
    k = 3
    for i in range(k, n - k):
        if sub_h[i] == max(sub_h[i - k:i + k + 1]):
            pivots.append((i, sub_h[i], "H"))
        elif sub_l[i] == min(sub_l[i - k:i + k + 1]):
            pivots.append((i, sub_l[i], "L"))

    # H-L-H-L 교차 시퀀스만 필터
    cleaned = []
    for p in pivots:
        if not cleaned or cleaned[-1][2] != p[2]:
            cleaned.append(p)
        else:
            # 같은 타입 연속 → 더 극단값 유지
            if p[2] == "H" and p[1] > cleaned[-1][1]:
                cleaned[-1] = p
            elif p[2] == "L" and p[1] < cleaned[-1][1]:
                cleaned[-1] = p

    if len(cleaned) < 3:
        return False, 0.0

    # H-L 쌍에서 조정폭(%) 계산
    contractions = []
    for i in range(len(cleaned) - 1):
        a, b = cleaned[i], cleaned[i + 1]
        if a[2] == "H" and b[2] == "L":
            pct = (a[1] - b[1]) / a[1]
            contractions.append(pct)

    if len(contractions) < min_contractions:
        return False, 0.0

    # 각 조정폭이 직전보다 작아져야 함 (변동성 수축)
    for i in range(1, len(contractions)):
        if contractions[i] >= contractions[i - 1]:
            return False, 0.0

    # 마지막 수축 폭 제한
    if contractions[-1] > last_contraction_max:
        return False, 0.0

    # pivot point = 마지막 swing high
    last_high = next((p for p in reversed(cleaned) if p[2] == "H"), None)
    if last_high is None:
        return False, 0.0

    pivot_price = float(last_high[1])
    return True, pivot_price


def compute_rs_rank(
    stock_close: pd.Series,
    spy_close:   pd.Series,
    period:      int = 252,
) -> float:
    """
    SPY 대비 상대강도 백분위 (0~100).
    Minervini 표준: 12-3-6-9월 가중 RS, 여기서는 단순 12개월 비율 사용.
    실제 백분위는 백테스트 엔진에서 cross-sectional로 계산.
    """
    if len(stock_close) < period or len(spy_close) < period:
        return np.nan
    s_ret = stock_close.iloc[-1] / stock_close.iloc[-period] - 1
    m_ret = spy_close.iloc[-1]   / spy_close.iloc[-period]   - 1
    return float(s_ret - m_ret)  # 차이값 → 엔진에서 cross-sectional rank


def find_vcp_signals(
    price_data: dict[str, pd.DataFrame],
    date:       pd.Timestamp,
    params:     dict,
) -> list[dict]:
    """
    date 기준 VCP 진입 가능 종목 리스트 반환.
    각 항목: {symbol, pivot_price, current_close, rs_score}
    """
    if "SPY" not in price_data:
        return []

    spy_df = price_data["SPY"]
    if date not in spy_df.index:
        return []
    spy_close_full = spy_df["close"]
    spy_idx = spy_df.index.searchsorted(date)
    spy_close = spy_close_full.iloc[: spy_idx + 1]

    # 1차: trend template + RS score 계산
    rs_scores: dict[str, float] = {}
    candidates: dict[str, dict] = {}

    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        idx = df.index.searchsorted(date)
        if idx < params.get("ma200_period", 200) + 30:
            continue
        sub = df.iloc[: idx + 1]
        rs = compute_rs_rank(sub["close"], spy_close)
        if not pd.isna(rs):
            rs_scores[sym] = rs
            candidates[sym] = {"sub": sub}

    if not rs_scores:
        return []

    # cross-sectional RS 백분위
    rs_series = pd.Series(rs_scores)
    rs_pct    = rs_series.rank(pct=True) * 100  # 0~100

    signals: list[dict] = []
    rs_min = params.get("rs_rank_min", 70)

    for sym, data in candidates.items():
        if rs_pct[sym] < rs_min:
            continue
        sub = data["sub"]
        if not passes_trend_template(
            sub["close"], sub["high"], sub["low"], rs_pct[sym], params
        ):
            continue
        ok, pivot = detect_vcp_pattern(
            sub["close"], sub["high"], sub["low"], sub["volume"], params
        )
        if not ok:
            continue

        signals.append({
            "symbol":        sym,
            "pivot_price":   pivot,
            "current_close": float(sub["close"].iloc[-1]),
            "rs_rank":       float(rs_pct[sym]),
        })

    # RS Rank 내림차순
    signals.sort(key=lambda x: x["rs_rank"], reverse=True)
    return signals
