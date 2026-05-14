"""
Phase 4-v2: 일목구름 선접근(Anticipatory Cloud) 전략

핵심 아이디어:
  현재가가 두꺼운 상승 구름을 향해 위에서 아래로 다가가고 있을 때,
  eta_min~eta_max 거래일 안에 구름 상단과 만날 것으로 예측되면 미리 매수.
  구름에 닿는 날 사는 게 아니라, 반등을 기다리며 미리 포지션 구축.

진입 조건 (bar t 종가 기준):
  1. close > 미래 구름 상단(senkou_a at t+eta) — 위에서 접근
  2. 미래 구름이 두껍고 상승형: senkou_a_future > senkou_b_future, 두께 >= thick_pct
  3. 가격이 구름을 향해 "완만히" 하락 중:
       -max_slope_pct_per_day <= slope < 0  (자유낙하 제외)
  4. 선형 외삽 meet_eta ∈ [eta_min, eta_max]
  5. 거리 필터 (ATR 단위): (close - sa_future) <= max_atr_distance × ATR20
  6. 추세 품질: tenkan >= kijun (눌림이지 반전이 아님)
  7. 중기 추세: chikou 양수 (close > close[t-26])
  8. 개별 종목 50MA 위 (하락추세 종목 제외)
  9. 유동성·가격 필터

손절: max(senkou_b_future × (1 - cloud_break_tol), entry - max_adverse_atr × ATR20)
익절: T1=1.5R(50%), T2=3.0R(30%), 잔여 20%는 엔진에서 cloud_mid 추격
"""
import numpy as np
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.indicators.ichimoku import ichimoku, future_cloud_at
from src.strategy.breakout_pullback import Signal


def _adx_components(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)

    alpha = 1 / period
    atr_w = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return adx, plus_di, minus_di


def generate_anticipatory_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[Signal]:
    entry_mode        = params.get("entry_mode", "anticipatory")
    tenkan_p          = params.get("tenkan_period", 9)
    kijun_p           = params.get("kijun_period", 26)
    senkou_b_p        = params.get("senkou_b_period", 52)
    shift             = params.get("chikou_offset", 26)
    eta_min           = params.get("eta_min", 2)
    eta_max           = params.get("eta_max", 5)
    slope_method      = params.get("slope_method", "reg10")
    reg_lookback      = params.get("slope_reg_lookback", 10)
    avg_lookback      = params.get("slope_avg_lookback", 5)
    thick_pct         = params.get("cloud_thickness_min_pct", 3.0) / 100
    max_atr_dist      = params.get("max_atr_distance", 3.0)   # ATR 단위 거리 (보조)
    max_cur_gap_pct   = params.get("max_current_cloud_gap_pct", 12.0) / 100  # 현재 구름 상단과 최대 이격
    max_fut_gap_pct   = params.get("max_future_cloud_gap_pct", 8.0) / 100    # 미래 구름과 최대 이격
    max_slope_pct_day  = params.get("max_slope_pct_per_day", 1.5) / 100   # 하루 최대 낙폭 (자유낙하 제외)
    max_upslope_pct_day = params.get("max_upslope_pct_per_day", 0.15) / 100  # 하루 최대 상승률 (박스권 상한)
    box_range_lookback = params.get("box_range_lookback", 30)              # 박스권 판정 기간
    box_range_max_pct  = params.get("box_range_max_pct", 8.0) / 100        # 박스권 최대 가격 범위 (%, high-low/close)
    cloud_tol         = params.get("cloud_break_tol", 0.005)
    stop_method       = params.get("stop_method", "cloud")   # "cloud" | "atr" | "hybrid"
    stop_atr_mult     = params.get("stop_atr_mult", 1.5)
    req_rising_cloud  = params.get("require_rising_cloud", True)  # 미래 구름 상단이 현재보다 높아야 함
    min_price         = params.get("min_price_usd", 10)
    min_vol           = params.get("min_avg_volume", 500_000)
    t1_r, t2_r        = params.get("partial_exit_r_multiples", [1.5, 3.0])
    w1, w2            = params.get("partial_exit_weights", [0.5, 0.3])
    req_tenkan_kijun  = params.get("require_tenkan_ge_kijun", True)
    req_chikou        = params.get("require_chikou_positive", True)
    req_50ma          = params.get("require_above_50ma", True)
    # 바 하단 추세선 (저가 선형회귀 기울기 > 0 = 상승 추세)
    req_rising_lows    = params.get("require_rising_lows", True)
    low_trend_lookback = params.get("low_trend_lookback", 20)
    med_trend_lookback = params.get("med_trend_lookback", 40)  # 중기 추세 확인용

    support_touch_tol  = params.get("support_touch_tolerance", 0.005)
    support_max_gap    = params.get("support_max_above_cloud_pct", 3.0) / 100
    support_bullish    = params.get("support_require_bullish_reclaim", True)
    support_tenkan     = params.get("support_require_tenkan_reclaim", True)
    use_adx_filter     = params.get("use_adx_filter", False)
    adx_period         = params.get("adx_period", 14)
    adx_min            = params.get("adx_min", 20.0)
    adx_plus_di        = params.get("adx_require_plus_di", True)
    adx_rising         = params.get("adx_require_rising", False)
    adx_rising_lookback = params.get("adx_rising_lookback", 3)
    use_high52_filter  = params.get("use_52w_high_filter", False)
    high52_min_ratio   = params.get("high52_min_ratio", 0.80)

    min_lookback = max(shift + eta_max + 10, senkou_b_p + 10, 50 + 5,
                       low_trend_lookback + 5, med_trend_lookback + 5)
    if len(df) < min_lookback:
        return []

    # ── 지표 계산 + numpy 변환 (루프 내 .iloc 제거용) ───────────────────
    atr_s   = calc_atr(df, 20)
    avg_vol = df["volume"].rolling(20).mean()
    ma50    = df["close"].rolling(50).mean()
    adx_s = plus_di_s = minus_di_s = None
    if use_adx_filter:
        adx_s, plus_di_s, minus_di_s = _adx_components(df, adx_period)
    high52_s = df["close"].rolling(252).max() if use_high52_filter else None

    ich = ichimoku(df, tenkan_p, kijun_p, senkou_b_p, shift)
    tenkan_s  = ich["tenkan"]
    kijun_s   = ich["kijun"]
    senkou_a_s = ich["senkou_a"]
    senkou_b_s = ich["senkou_b"]

    # 각 k에 대해 미래 구름 좌표 사전 계산 (lookahead 없음)
    fc: dict[int, pd.DataFrame] = {}
    for k in range(eta_min, eta_max + 1):
        fc[k] = future_cloud_at(df, k, tenkan_p, kijun_p, senkou_b_p, shift)

    # ── numpy 배열로 미리 추출 (루프 내 pandas .iloc 호출 제거) ──────────
    close_arr  = df["close"].values
    high_arr   = df["high"].values
    low_arr    = df["low"].values
    vol_arr    = avg_vol.values
    atr_arr    = atr_s.values
    ma50_arr   = ma50.values
    tenkan_arr = tenkan_s.values
    kijun_arr  = kijun_s.values
    sa_arr     = senkou_a_s.values
    sb_arr     = senkou_b_s.values
    fc_sa = {k: fc[k]["senkou_a_future"].values for k in range(eta_min, eta_max + 1)}
    fc_sb = {k: fc[k]["senkou_b_future"].values for k in range(eta_min, eta_max + 1)}
    adx_arr     = adx_s.values     if adx_s     is not None else None
    plus_di_arr = plus_di_s.values if plus_di_s is not None else None
    minus_di_arr= minus_di_s.values if minus_di_s is not None else None
    high52_arr  = high52_s.values  if high52_s  is not None else None

    lookback = max(reg_lookback, avg_lookback, shift + eta_max, 55)

    signals: list[Signal] = []
    seen: set = set()

    for i in range(lookback, len(df) - 1):
        close_t = close_arr[i]
        if close_t < min_price:
            continue
        if np.isnan(vol_arr[i]) or vol_arr[i] < min_vol:
            continue
        if use_high52_filter:
            high52 = high52_arr[i]
            if np.isnan(high52) or high52 <= 0 or close_t / high52 < high52_min_ratio:
                continue
        if use_adx_filter:
            adx_val  = adx_arr[i]
            plus_di  = plus_di_arr[i]
            minus_di = minus_di_arr[i]
            if np.isnan(adx_val) or adx_val < adx_min:
                continue
            if adx_plus_di and (np.isnan(plus_di) or np.isnan(minus_di) or plus_di <= minus_di):
                continue
            if adx_rising:
                prev_idx = i - adx_rising_lookback
                if prev_idx < 0 or np.isnan(adx_arr[prev_idx]) or adx_val <= adx_arr[prev_idx]:
                    continue

        # ── 현재 구름 위에 있는지 확인 (구름 안에서 매수 방지) ────────────
        cur_sa = sa_arr[i]
        cur_sb = sb_arr[i]
        if np.isnan(cur_sa) or np.isnan(cur_sb):
            continue
        cur_cloud_top = max(cur_sa, cur_sb)
        cur_cloud_bottom = min(cur_sa, cur_sb)
        if close_t <= cur_cloud_top:  # 현재 구름 상단 아래면 제외
            continue
        cur_gap_pct = (close_t - cur_cloud_top) / close_t
        if entry_mode == "confirmed_support":
            # Confirmed support waits for a current-cloud touch and reclaim.
            if cur_sa <= cur_sb:
                continue
            if df["low"].iloc[i] > cur_cloud_top * (1 + support_touch_tol):
                continue
            if cur_gap_pct > support_max_gap:
                continue
            if support_bullish:
                prev_close = df["close"].iloc[i - 1]
                if close_t <= df["open"].iloc[i] and close_t <= prev_close:
                    continue
            if support_tenkan:
                tk_val = tenkan_s.iloc[i]
                if pd.isna(tk_val) or close_t < tk_val:
                    continue
        # 구름 상단과 최소 3% 이상 이격 (TPR형 rising-cloud squeeze 방지)
        if entry_mode != "confirmed_support" and cur_gap_pct < 0.03:
            continue
        # 현재 구름이 너무 멀리 아래 있으면 제외 (FCX/CVNA형 — 구름이 급등해서 가격을 따라잡는 상황)
        if entry_mode != "confirmed_support" and cur_gap_pct > max_cur_gap_pct:
            continue
        # ── 최근 15봉 내 구름 터치 없어야 함 (두 번째 접근 방지) ─────────
        recent_touch = False
        for j in range(max(0, i - 15), i):
            c_j  = close_arr[j]
            sa_j = sa_arr[j]
            sb_j = sb_arr[j]
            if np.isnan(sa_j) or np.isnan(sb_j):
                continue
            if c_j <= max(sa_j, sb_j) * 1.01:
                recent_touch = True
                break
        if entry_mode != "confirmed_support" and recent_touch:
            continue

        # ── 중기 추세 확인 (하락 중 구름 터치 방지) ────────────────────────
        med_start = max(0, i - med_trend_lookback + 1)
        med_window = close_arr[med_start: i + 1]
        if len(med_window) >= 10:
            x_med = np.arange(len(med_window), dtype=float)
            slope_med = np.polyfit(x_med, med_window, 1)[0]
            if slope_med < 0:
                continue

        # ── 박스권 확인: 최근 N봉 고저 범위가 좁아야 함 ───────────────
        box_start = max(0, i - box_range_lookback + 1)
        box_hi = high_arr[box_start: i + 1].max()
        box_lo = low_arr[box_start: i + 1].min()
        if (box_hi - box_lo) / close_t > box_range_max_pct:
            continue

        # ── 눌림 확인: 최근 20봉 내 현재가보다 2% 이상 높은 고점 있어야 함
        recent_high = close_arr[max(0, i - 20): i].max() if i > 0 else close_t
        if recent_high < close_t * 1.02:
            continue

        # ── 추세 품질 필터 ──────────────────────────────────────────────
        tk = tenkan_arr[i]
        kj = kijun_arr[i]
        if req_tenkan_kijun:
            if np.isnan(tk) or np.isnan(kj) or tk < kj:
                continue

        # 종가 > kijun + 1% 마진
        if not np.isnan(kj) and close_t < kj * 1.01:
            continue

        if req_chikou:
            idx_back = i - shift
            if idx_back < 0:
                continue
            if close_t <= close_arr[idx_back]:
                continue

        if req_50ma:
            ma = ma50_arr[i]
            if np.isnan(ma) or close_t <= ma:
                continue
            ma_prev = ma50_arr[i - 10] if i >= 10 else ma50_arr[0]
            if np.isnan(ma_prev) or ma <= ma_prev:
                continue

        # ── 바 하단 추세선: 저가 상승 추세 확인 ────────────────────────
        if req_rising_lows:
            lows_start = max(0, i - low_trend_lookback + 1)
            lows_window = low_arr[lows_start: i + 1]
            if len(lows_window) < 5:
                continue
            x_low = np.arange(len(lows_window), dtype=float)
            slope_low = np.polyfit(x_low, lows_window, 1)[0]
            if slope_low <= 0:
                continue

        # ── 가격 추세 기울기 ────────────────────────────────────────────
        if slope_method == "reg10":
            win_start = max(0, i - reg_lookback + 1)
            window = close_arr[win_start: i + 1]
            if len(window) < 3:
                continue
            x = np.arange(len(window), dtype=float)
            slope = np.polyfit(x, window, 1)[0]
        else:  # avg5
            win_start = max(0, i - avg_lookback + 1)
            window = close_arr[win_start: i + 1]
            if len(window) < 2:
                continue
            slope = float(np.mean(np.diff(window)))

        # 박스권 확인: 자유낙하와 강한 상승추세 모두 제외
        if entry_mode != "confirmed_support":
            if slope < -close_t * max_slope_pct_day:
                continue
            if slope > close_t * max_upslope_pct_day:
                continue

        atr_val = atr_arr[i]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        # ── 1단계: eta 윈도우 전체가 두꺼운 주황 구름 블록인지 확인 ───────
        window_range = range(eta_min, eta_max + 1)
        thick_ok_count = 0
        for k in window_range:
            sa_k = fc_sa[k][i]
            sb_k = fc_sb[k][i]
            if np.isnan(sa_k) or np.isnan(sb_k):
                continue
            rising_ok = (not req_rising_cloud) or (sa_k > cur_sa)
            if sa_k > sb_k and (sa_k - sb_k) / close_t >= thick_pct and rising_ok:
                thick_ok_count += 1
        if thick_ok_count < len(window_range):
            continue

        # ── 2단계: meet_eta 탐색 (우상향 구름이 현재 주가에 도달하는 시점) ──
        meet_eta = None
        meet_sa = meet_sb = 0.0
        if entry_mode == "confirmed_support":
            meet_eta = 0
            meet_sa = cur_cloud_top
            meet_sb = cur_cloud_bottom
            window_range = []

        if meet_eta is None:
            # 구름이 우상향인지 먼저 확인 (eta_min → eta_max 구간에서 sa가 증가해야)
            sa_at_min = fc_sa[eta_min][i]
            sa_at_max = fc_sa[eta_max][i]
            if pd.isna(sa_at_min) or pd.isna(sa_at_max):
                continue
            if sa_at_max <= sa_at_min:  # 구름이 상승하지 않으면 제외
                continue

            # 사전 필터: eta_max 시점에도 구름이 주가와 너무 멀면 전체 skip
            if (close_t - sa_at_max) / close_t > max_fut_gap_pct:
                continue

            # ETA = 구름 상단이 현재 주가 레벨에 실제로 "닿는" 최초 k
            # eta_reach_pct 이내여야 "도달"로 인정 — 구름이 실제로 주가 레벨까지 올라와야 함
            eta_reach_pct = params.get("eta_reach_pct", 3.0) / 100
            for k in window_range:
                sa_fut = fc_sa[k][i]
                sb_fut = fc_sb[k][i]
                if np.isnan(sa_fut) or np.isnan(sb_fut):
                    continue
                gap_pct = (close_t - sa_fut) / close_t
                if gap_pct < -0.01:
                    break
                if gap_pct <= eta_reach_pct:
                    if (close_t - sa_fut) > max_atr_dist * atr_val:
                        break
                    # 도달 시점의 구름이 실제로 두꺼운지 재확인
                    meet_thickness = (sa_fut - sb_fut) / close_t
                    if sa_fut <= sb_fut or meet_thickness < thick_pct:
                        continue
                    meet_eta = k
                    meet_sa  = sa_fut
                    meet_sb  = sb_fut
                    break

        if meet_eta is None:
            continue

        # ── 신호 생성 ────────────────────────────────────────────────────
        entry_loc = i + 1
        entry_date = df.index[entry_loc]
        if entry_date in seen:
            continue

        entry_price = df["open"].iloc[entry_loc]
        if entry_price < min_price:
            continue
        # 갭업 진입 방지: 다음날 시가가 신호봉 종가 대비 2% 초과 갭업이면 스킵 (TECH형)
        if entry_price > close_t * 1.02:
            continue

        # 손절 계산: stop_method에 따라 세 가지 방식
        cloud_stop = meet_sb * (1 - cloud_tol)
        atr_stop   = entry_price - stop_atr_mult * atr_val
        if stop_method == "atr":
            stop = atr_stop
        elif stop_method == "hybrid":
            # 두 기준 중 더 높은(entry에 가까운) 쪽 사용 — thesis는 구름으로 보호하되 R 축소
            stop = max(cloud_stop, atr_stop)
        else:  # "cloud" (기본값)
            stop = cloud_stop

        r = entry_price - stop
        if r <= 0 or r / entry_price > 0.30:   # 너무 얇은 구름이나 비정상 케이스 제거
            continue

        signals.append(Signal(
            symbol          = symbol,
            entry_date      = entry_date,
            entry_price     = entry_price,
            stop            = stop,
            r               = r,
            targets         = [entry_price + t1_r * r, entry_price + t2_r * r],
            partial_weights = [w1, w2],
            trail_period    = kijun_p,
        ))
        seen.add(entry_date)

    return signals
