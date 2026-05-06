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


def generate_anticipatory_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[Signal]:
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
    max_slope_pct_day = params.get("max_slope_pct_per_day", 1.5) / 100  # 하루 최대 낙폭
    cloud_tol         = params.get("cloud_break_tol", 0.005)
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

    min_lookback = max(shift + eta_max + 10, senkou_b_p + 10, 50 + 5,
                       low_trend_lookback + 5, med_trend_lookback + 5)
    if len(df) < min_lookback:
        return []

    # ── 지표 계산 ─────────────────────────────────────────────────────────
    atr_s   = calc_atr(df, 20)
    avg_vol = df["volume"].rolling(20).mean()
    ma50    = df["close"].rolling(50).mean()

    ich = ichimoku(df, tenkan_p, kijun_p, senkou_b_p, shift)
    tenkan_s  = ich["tenkan"]
    kijun_s   = ich["kijun"]
    senkou_a_s = ich["senkou_a"]
    senkou_b_s = ich["senkou_b"]

    # 각 k에 대해 미래 구름 좌표 사전 계산 (lookahead 없음)
    fc: dict[int, pd.DataFrame] = {}
    for k in range(eta_min, eta_max + 1):
        fc[k] = future_cloud_at(df, k, tenkan_p, kijun_p, senkou_b_p, shift)

    lookback = max(reg_lookback, avg_lookback, shift + eta_max, 55)

    signals: list[Signal] = []
    seen: set = set()

    for i in range(lookback, len(df) - 1):
        close_t = df["close"].iloc[i]
        if close_t < min_price:
            continue
        if avg_vol.iloc[i] < min_vol:
            continue

        # ── 현재 구름 위에 있는지 확인 (구름 안에서 매수 방지) ────────────
        cur_sa = senkou_a_s.iloc[i]
        cur_sb = senkou_b_s.iloc[i]
        if pd.isna(cur_sa) or pd.isna(cur_sb):
            continue
        cur_cloud_top = max(cur_sa, cur_sb)
        if close_t <= cur_cloud_top:  # 현재 구름 상단 아래면 제외
            continue
        cur_gap_pct = (close_t - cur_cloud_top) / close_t
        # 구름 상단과 최소 3% 이상 이격 (TPR형 rising-cloud squeeze 방지)
        if cur_gap_pct < 0.03:
            continue
        # 현재 구름이 너무 멀리 아래 있으면 제외 (FCX/CVNA형 — 구름이 급등해서 가격을 따라잡는 상황)
        if cur_gap_pct > max_cur_gap_pct:
            continue
        # ── 최근 15봉 내 구름 터치 없어야 함 (두 번째 접근 방지) ─────────
        recent_touch = False
        for j in range(max(0, i - 15), i):
            c_j  = df["close"].iloc[j]
            sa_j = senkou_a_s.iloc[j]
            sb_j = senkou_b_s.iloc[j]
            if pd.isna(sa_j) or pd.isna(sb_j):
                continue
            if c_j <= max(sa_j, sb_j) * 1.01:
                recent_touch = True
                break
        if recent_touch:
            continue

        # ── 중기 추세 확인 (하락 중 구름 터치 방지) ────────────────────────
        med_window = df["close"].iloc[max(0, i - med_trend_lookback + 1): i + 1].values
        if len(med_window) >= 10:
            x_med = np.arange(len(med_window), dtype=float)
            slope_med = np.polyfit(x_med, med_window, 1)[0]
            if slope_med < 0:  # 중기(40봉) 종가 추세가 하락이면 제외
                continue

        # ── 눌림 확인: 최근 20봉 내 현재가보다 2% 이상 높은 고점 있어야 함
        recent_high = df["close"].iloc[max(0, i - 20): i].max()
        if recent_high < close_t * 1.02:
            continue

        # ── 추세 품질 필터 ──────────────────────────────────────────────
        if req_tenkan_kijun:
            tk = tenkan_s.iloc[i]
            kj = kijun_s.iloc[i]
            if pd.isna(tk) or pd.isna(kj) or tk < kj:
                continue

        # 종가 > kijun + 1% 마진 (겨우 통과하는 약세 케이스 제거)
        kj_val = kijun_s.iloc[i]
        if not pd.isna(kj_val) and close_t < kj_val * 1.01:
            continue

        if req_chikou:
            # chikou = close[t] > close[t-shift] (후행스팬 양수)
            idx_back = i - shift
            if idx_back < 0:
                continue
            close_back = df["close"].iloc[idx_back]
            if close_t <= close_back:
                continue

        if req_50ma:
            ma = ma50.iloc[i]
            if pd.isna(ma) or close_t <= ma:
                continue
            # 50MA 자체가 상승 중이어야 함 (10봉 전보다 높아야)
            ma_prev = ma50.iloc[i - 10] if i >= 10 else ma50.iloc[0]
            if pd.isna(ma_prev) or ma <= ma_prev:
                continue

        # ── 바 하단 추세선: 저가 상승 추세 확인 ────────────────────────
        if req_rising_lows:
            lows_window = df["low"].iloc[max(0, i - low_trend_lookback + 1): i + 1].values
            if len(lows_window) < 5:
                continue
            x_low = np.arange(len(lows_window), dtype=float)
            slope_low = np.polyfit(x_low, lows_window, 1)[0]
            if slope_low <= 0:
                continue

        # ── 가격 추세 기울기 ────────────────────────────────────────────
        if slope_method == "reg10":
            window = df["close"].iloc[max(0, i - reg_lookback + 1): i + 1].values
            if len(window) < 3:
                continue
            x = np.arange(len(window), dtype=float)
            slope = np.polyfit(x, window, 1)[0]
        else:  # avg5
            window = df["close"].iloc[max(0, i - avg_lookback + 1): i + 1].values
            if len(window) < 2:
                continue
            slope = float(np.mean(np.diff(window)))

        # 하락이어야 하고, 자유낙하는 제외
        if slope >= 0:
            continue
        if slope < -close_t * max_slope_pct_day:
            continue

        atr_val = atr_s.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        # ── 1단계: eta 윈도우 전체가 두꺼운 주황 구름 블록인지 확인 ───────
        # "며칠 뒤에 앞에서 만나게 될 두꺼운 구름" → 단일 point가 아닌 블록이어야 함
        window_range = range(eta_min, eta_max + 1)
        thick_ok_count = 0
        for k in window_range:
            sa_k = fc[k]["senkou_a_future"].iloc[i]
            sb_k = fc[k]["senkou_b_future"].iloc[i]
            if pd.isna(sa_k) or pd.isna(sb_k):
                continue
            if sa_k > sb_k and (sa_k - sb_k) / close_t >= thick_pct:
                thick_ok_count += 1
        # 윈도우 내 모든 bar가 두꺼운 상승 구름이어야 진입 고려
        if thick_ok_count < len(window_range):
            continue

        # ── 2단계: meet_eta 탐색 (ATR 거리 + 선형 외삽) ────────────────
        meet_eta = None
        meet_sa = meet_sb = 0.0

        for k in window_range:
            sa_fut = fc[k]["senkou_a_future"].iloc[i]
            sb_fut = fc[k]["senkou_b_future"].iloc[i]
            if pd.isna(sa_fut) or pd.isna(sb_fut):
                continue
            # 현재가가 구름 위인지
            if close_t <= sa_fut:
                continue
            # 퍼센트 기반 거리 필터: 미래 구름 상단이 현재가 대비 너무 멀면 제외
            if (close_t - sa_fut) / close_t > max_fut_gap_pct:
                continue
            # ATR 기반 보조 필터
            if (close_t - sa_fut) > max_atr_dist * atr_val:
                continue
            # 선형 외삽 가격이 구름 상단에 닿는지
            price_proj = close_t + slope * k
            if price_proj <= sa_fut:
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

        # 손절 = 구름 하단 아래 (cloud_break_tol 버퍼)
        # 구름 도달까지 예정된 하락 경로 → ATR 손절 사용 불가 (구름 전에 손절됨)
        stop = meet_sb * (1 - cloud_tol)

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
