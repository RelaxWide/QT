"""
Clenow / Weinstein 실시간 신호 생성 (당일 기준).

market 인자는 신호 자체는 시장 독립적이라 영향 작지만,
- regime index 제외 (SPY vs ^KS11)
- 주봉 리샘플 freq (W-WED vs W-FRI)
두 가지만 시장별로 다르다.
"""
import pandas as pd

from src.strategy.clenow_momentum import compute_scores, atr_pct_at, size_multiplier
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
    regime_ok: bool = True,
    sectors: dict | None = None,
    max_per_sector: int | None = None,
) -> dict:
    """
    regime_ok:      SPY > MA200 여부 (호출측에서 판정). False 면 신규 매수 후보를 내지 않는다 (매도는 그대로).
    sectors:        {종목: 업종}. max_per_sector 와 함께 주면 보유 + 신규 합산으로 업종당 종목 수를 제한한다.
    Returns:
        sell_ma100:  MA100 이탈 종목 (매일 체크)
        sell_ranked: 모멘텀 상위 N 밖으로 밀려난 종목 (수요일에만)
        buy:         신규 진입 후보 (수요일에만, 순위순 — 앞에서부터 빈자리만큼 산다)
        size_mult:   {매수 후보: 슬롯 금액 배율} — 변동성 역가중
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
        if regime_ok:
            new_buys = [s for s in top_syms_ord if s not in holdings]   # score 순서 유지

        # 업종 제한 — 남는 보유분 + 앞선 후보를 합쳐 업종당 max_per_sector 까지
        if new_buys and sectors and max_per_sector:
            counts: dict = {}
            for s in holdings:
                if s not in sell_ma100 and s not in sell_ranked:
                    sec = sectors.get(s)
                    if sec:
                        counts[sec] = counts.get(sec, 0) + 1
            kept = []
            for s in new_buys:
                sec = sectors.get(s)
                if sec and counts.get(sec, 0) >= max_per_sector:
                    continue
                if sec:
                    counts[sec] = counts.get(sec, 0) + 1
                kept.append(s)
            new_buys = kept

    atr_p = cl_p.get("atr_period", 20)
    size_mult = {s: round(size_multiplier(atr_pct_at(price_data[s], today, atr_p), cl_p), 3)
                 for s in new_buys if s in price_data}

    return {
        "sell_ma100":    sell_ma100,
        "sell_ranked":   sell_ranked,
        "buy":           new_buys,
        "size_mult":     size_mult,
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
