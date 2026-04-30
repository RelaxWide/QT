"""
Connors RSI Mean Reversion 전략

진입 조건:
  1. 종가 > 200일 SMA (장기 추세 상승)
  2. ConnorsRSI < entry_threshold (기본 5)
  3. 거래량 필터: 50일 평균 거래량 >= 500K
  4. 최소가 >= min_price_usd

청산 조건:
  - ConnorsRSI > exit_threshold (기본 70) 또는
  - 종가 > 5일 SMA (단기 반등 완료)
  - 손절: 진입가 -5% (시장 급락 보호)

리스크: 자본의 10%, 최대 5종목 동시 보유
"""
import numpy as np
import pandas as pd

from src.indicators.connors_rsi import connors_rsi


def find_connors_signals(
    price_data: dict[str, pd.DataFrame],
    date:       pd.Timestamp,
    params:     dict,
    crsi_cache: dict[str, pd.Series],
    ma200_cache: dict[str, pd.Series],
    vol_cache:   dict[str, pd.Series],
) -> list[dict]:
    """date 기준 진입 후보 종목 (CRSI 낮은 순)."""
    entry_thr = params.get("entry_threshold", 5)
    min_price = params.get("min_price_usd",  10)
    min_vol   = params.get("min_avg_volume", 500000)

    candidates = []
    for sym, df in price_data.items():
        if sym == "SPY" or date not in df.index:
            continue
        c     = df.loc[date, "close"]
        if c < min_price:
            continue
        ma200 = ma200_cache[sym].loc[date] if date in ma200_cache[sym].index else np.nan
        if pd.isna(ma200) or c <= ma200:
            continue
        v_avg = vol_cache[sym].loc[date] if date in vol_cache[sym].index else np.nan
        if pd.isna(v_avg) or v_avg < min_vol:
            continue
        crsi = crsi_cache[sym].loc[date] if date in crsi_cache[sym].index else np.nan
        if pd.isna(crsi) or crsi >= entry_thr:
            continue
        candidates.append({"symbol": sym, "crsi": float(crsi), "close": float(c)})

    candidates.sort(key=lambda x: x["crsi"])  # 가장 oversold 먼저
    return candidates
