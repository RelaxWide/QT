"""
ADM 엔진 — 월말 1자산 holding, 다음 거래일 시가 리밸런싱
"""
import pandas as pd

from src.strategy.adm import momentum_score, select_asset
from src.backtest.costs import make_cost_model


def run_adm_backtest(price_data: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg     = cfg.get("adm_strategy", {})
    cap       = cfg["backtest"]["initial_capital_usd"]
    slip      = cfg["risk"]["slippage_pct"] / 100
    offensive = s_cfg.get("offensive", ["SPY", "SCZ"])
    defensive = s_cfg.get("defensive", "BND")
    cost      = make_cost_model(cfg)

    universe = [s for s in offensive + [defensive] if s in price_data]
    if not universe:
        return pd.Series(dtype=float), []

    # 월말 종가 기준 점수
    monthly_close = {sym: price_data[sym]["close"].resample("ME").last() for sym in universe}
    scores = {sym: momentum_score(monthly_close[sym]) for sym in offensive if sym in price_data}

    # 모든 거래일 합집합
    all_dates = sorted({d for sym in universe for d in price_data[sym].index})

    # 월말 일자에서 다음달 첫 영업일에 리밸런싱
    cash = float(cap)
    holding_sym = None
    shares = 0.0
    entry_px = entry_date = None
    trades = []
    eq = []

    pending_target = None  # 다음 영업일에 리밸런싱할 자산
    last_period = None

    # 월말 신호 사전 계산 (월말 timestamp → target asset)
    targets = {}
    monthly_ends = sorted(set(monthly_close[universe[0]].index))
    for me in monthly_ends:
        tgt = select_asset(scores, offensive, defensive, me) if all(me in scores[s].index for s in offensive if s in scores) else None
        if tgt:
            targets[me.to_period("M")] = tgt

    prev_month = None
    for date in all_dates:
        period = pd.Timestamp(date).to_period("M")

        # 새로운 달의 첫 영업일 → 직전월 신호로 리밸런싱
        if prev_month is not None and period != prev_month:
            tgt = targets.get(prev_month)
            if tgt is not None and tgt != holding_sym:
                # 청산
                if holding_sym is not None and shares > 0 and date in price_data[holding_sym].index:
                    px = price_data[holding_sym].loc[date, "open"] * (1 - slip)
                    sell_c = cost.sell_cost(px * shares)
                    pnl = (px - entry_px) * shares - sell_c
                    cash += px * shares - sell_c
                    trades.append({"symbol": holding_sym, "entry_date": entry_date,
                                   "exit_date": date, "entry_price": entry_px,
                                   "exit_price": px, "shares": shares, "pnl": pnl})
                    shares = 0.0
                    holding_sym = None
                # 진입
                if tgt in price_data and date in price_data[tgt].index:
                    px = price_data[tgt].loc[date, "open"] * (1 + slip)
                    alloc = cash * 0.98
                    buy_c = cost.buy_cost(alloc)
                    shares = (alloc - buy_c) / px
                    cash -= shares * px + buy_c
                    holding_sym = tgt
                    entry_px = px
                    entry_date = date
        prev_month = period

        # 일별 자산 평가
        if holding_sym is not None and date in price_data[holding_sym].index:
            equity = cash + shares * price_data[holding_sym].loc[date, "close"]
        else:
            equity = cash + (shares * entry_px if shares > 0 else 0)
        eq.append({"date": date, "equity": equity})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
