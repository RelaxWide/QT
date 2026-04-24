"""
Clenow / Weinstein 실시간 신호 생성 (당일 기준)
"""
import pandas as pd

from src.strategy.clenow_momentum import compute_scores
from src.strategy.weinstein_stage2 import generate_weinstein_signals, _resample_weekly


def get_clenow_signals(
    price_data: dict,
    holdings: set[str],
    cfg: dict,
    today: pd.Timestamp,
    is_wednesday: bool,
) -> dict:
    """
    Returns:
        sell: MA100 이탈 종목 (매일 체크)
        buy:  신규 진입 후보 (수요일에만)
        rebalance: 탈락 종목 매도 (수요일에만)
    """
    cl_p    = cfg.get("clenow_strategy", {})
    ma100_p = cl_p.get("ma100_period", 100)
    max_pos = cl_p.get("max_positions", 20)

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
    new_buys    = []
    sell_ranked = []
    if is_wednesday:
        scores   = compute_scores(price_data, today, cl_p)
        top_syms = set(sorted(scores, key=lambda s: scores[s], reverse=True)[:max_pos])

        sell_ranked = [s for s in holdings if s not in top_syms and s not in sell_ma100]
        new_buys    = [s for s in top_syms if s not in holdings]

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
        sell: MA30 이탈 종목 (매일 체크)
        buy:  Stage 2 돌파 후보 (수요일에만)
    """
    w_p    = cfg.get("weinstein_strategy", {})
    ma30_p = w_p.get("ma30_period", 30)

    # 매일: MA30 이탈 청산
    sell_ma30 = []
    for sym in list(holdings):
        df_sym = price_data.get(sym)
        if df_sym is None or today not in df_sym.index:
            continue
        c   = df_sym.loc[today, "close"]
        wdf = _resample_weekly(df_sym)
        ma30_weekly = wdf["close"].rolling(ma30_p).mean()
        # forward-fill to today
        ma30_val = ma30_weekly.reindex([today], method="ffill")
        if not ma30_val.empty and pd.notna(ma30_val.iloc[0]) and c < ma30_val.iloc[0]:
            sell_ma30.append(sym)

    # 수요일: Stage 2 돌파 스캔
    new_buys = []
    if is_wednesday:
        for sym, df in price_data.items():
            if sym == "SPY" or sym in holdings:
                continue
            sigs = generate_weinstein_signals(sym, df, w_p)
            # 오늘 또는 어제(수요일)가 entry_date인 신호
            recent = [s for s in sigs if s.entry_date >= today]
            if recent:
                new_buys.append(sym)

    return {
        "sell_ma30": sell_ma30,
        "buy":       new_buys,
    }
