import pandas as pd


def ichimoku(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    shift: int = 26,
) -> pd.DataFrame:
    """
    반환값 (모두 bar t 시점에서 lookahead 없음):
      tenkan  : 단기 전환선
      kijun   : 중기 기준선
      senkou_a: 선행스팬 A (shift 봉 앞 계산값이 현재 bar에 표시)
      senkou_b: 선행스팬 B (동일)

    Chikou 조건은 caller에서 inline으로 계산:
      cond_chikou = df['close'] > df['close'].shift(shift)
    """
    hi, lo = df["high"], df["low"]

    tenkan = (hi.rolling(tenkan_period).max() + lo.rolling(tenkan_period).min()) / 2
    kijun  = (hi.rolling(kijun_period).max()  + lo.rolling(kijun_period).min())  / 2

    # Senkou A/B: 현재 bar t에 표시되는 값 = shift 봉 전에 계산된 값
    # → .shift(shift) 적용 시 bar t의 값은 bar t-shift 데이터만 사용 (lookahead 없음)
    senkou_a = ((tenkan + kijun) / 2).shift(shift)
    senkou_b = ((hi.rolling(senkou_b_period).max() + lo.rolling(senkou_b_period).min()) / 2).shift(shift)

    return pd.DataFrame(
        {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b},
        index=df.index,
    )


def future_cloud_at(
    df: pd.DataFrame,
    k_ahead: int,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    shift: int = 26,
) -> pd.DataFrame:
    """
    bar t에서 t+k 시점에 그려질 구름 좌표 (lookahead 없음).

    원리: senkou_a[t] = unshifted_a[t-26], 따라서
          senkou_a[t+k] = unshifted_a[t+k-26] = unshifted_a.shift(26-k) at t.
    k <= shift(=26) 필수. 실용 범위: k=1~10.
    """
    if k_ahead > shift:
        raise ValueError(f"k_ahead ({k_ahead}) must be <= shift ({shift})")
    hi, lo = df["high"], df["low"]
    tenkan_raw   = (hi.rolling(tenkan_period).max() + lo.rolling(tenkan_period).min()) / 2
    kijun_raw    = (hi.rolling(kijun_period).max()  + lo.rolling(kijun_period).min())  / 2
    senkou_b_raw = (hi.rolling(senkou_b_period).max() + lo.rolling(senkou_b_period).min()) / 2
    lag = shift - k_ahead
    senkou_a_future = ((tenkan_raw + kijun_raw) / 2).shift(lag)
    senkou_b_future = senkou_b_raw.shift(lag)
    return pd.DataFrame(
        {"senkou_a_future": senkou_a_future, "senkou_b_future": senkou_b_future},
        index=df.index,
    )
