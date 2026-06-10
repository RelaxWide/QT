"""
Clenow / Weinstein 실시간 신호 생성 (당일 기준).

market 인자는 신호 자체는 시장 독립적이라 영향 작지만,
- regime index 제외 (SPY vs ^KS11)
- 주봉 리샘플 freq (W-WED vs W-FRI)
두 가지만 시장별로 다르다.
"""
import pandas as pd

from src.strategy.clenow_momentum import compute_scores
from src.strategy.weinstein_stage2 import generate_weinstein_signals, _resample_weekly


def _index_ticker(cfg: dict) -> str:
    """cfg market.regime_index 우선, 없으면 SPY."""
    return cfg.get("market", {}).get("regime_index", "SPY")


def get_clenow_signals(
    price_data: dict,
    holdings: set[str],
    cfg: dict,
    today: pd.Timestamp,
    is_wednesday: bool,
) -> dict:
    """
    Returns:
        sell_ma100:  MA100 이탈 종목 (매일 체크)
        sell_ranked: 모멘텀 상위 N 밖으로 밀려난 종목 (수요일에만)
        buy:         신규 진입 후보 (수요일에만)
    """
    cl_p    = dict(cfg.get("clenow_strategy", {}))   # 변형 방지 사본
    ma100_p = cl_p.get("ma100_period", 100)
    max_pos = cl_p.get("max_positions", 20)
    cl_p.setdefault("index_ticker", _index_ticker(cfg))

    # 매일: MA100 이탈 청산
    sell_ma100 = []
    for sym in list(holdings):
        df_sym = price_data.get(sym)
        if df_sym is None or today not in df_sym.index:
            continue
        c = df_sym.loc[today, "close"]
        ma100 = df_sym["close"].rolling(ma100_p).mean()
        if today in ma100.index:
            mv = ma100.loc[today]
            if pd.notna(mv) and c < mv:
                sell_ma100.append(sym)

    # 수요일: 스코어 기반 리밸런싱
    # set 으로 변환하면 순회 순서 비결정적 (PYTHONHASHSEED 영향) → score 순서 유지를 위해 list 사용
    new_buys    = []
    sell_ranked = []
    if is_wednesday:
        scores       = compute_scores(price_data, today, cl_p)
        top_syms_ord = sorted(scores, key=lambda s: scores[s], reverse=True)[:max_pos]
        top_set      = set(top_syms_ord)

        sell_ranked = [s for s in holdings if s not in top_set and s not in sell_ma100]
        new_buys    = [s for s in top_syms_ord if s not in holdings]   # score 순서 유지

    return {
        "sell_ma100":    sell_ma100,
        "sell_ranked":   sell_ranked,
        "buy":           new_buys,
    }


def get_weinstein_signals(
    price_data: dict,
    holdings: set[str],
    cfg: dict,
    today: pd.Timestamp,
    is_wednesday: bool,
) -> dict:
    """
    Returns:
        sell_ma30: MA30 이탈 종목 (매일 체크)
        buy:       Stage 2 돌파 후보 (수요일에만)
    """
    w_p    = dict(cfg.get("weinstein_strategy", {}))
    ma30_p = w_p.get("ma30_period", 30)
    weekly_freq = w_p.get("weekly_freq", "W-WED")
    idx_ticker  = _index_ticker(cfg)

    # 매일: MA30 이탈 청산
    sell_ma30 = []
    for sym in list(holdings):
        df_sym = price_data.get(sym)
        if df_sym is None or today not in df_sym.index:
            continue
        c   = df_sym.loc[today, "close"]
        wdf = _resample_weekly(df_sym, freq=weekly_freq)
        ma30_weekly = wdf["close"].rolling(ma30_p).mean()
        ma30_val = ma30_weekly.reindex([today], method="ffill")
        if not ma30_val.empty and pd.notna(ma30_val.iloc[0]) and c < ma30_val.iloc[0]:
            sell_ma30.append(sym)

    # 수요일: Stage 2 돌파 스캔 (최근 6일 내 신호)
    new_buys = []
    if is_wednesday:
        for sym, df in price_data.items():
            if sym == idx_ticker or sym in holdings:
                continue
            if sym in ("SPY", "^KS11", "^KS200", "^VIX", "^VKOSPI"):
                continue
            sigs = generate_weinstein_signals(sym, df, w_p)
            recent = [s for s in sigs if s.signal_week >= today - pd.Timedelta(days=6)]
            if recent:
                new_buys.append(sym)

    return {
        "sell_ma30": sell_ma30,
        "buy":       new_buys,
    }
