"""
범용 스윙 백테스트 엔진 — Pocket Pivot / Darvas / ADX Donchian / OBV / BB Squeeze 공용

청산 우선순위:
  1. open ≤ stop → 갭다운 손절
  2. low ≤ stop → 장중 손절
  3. trail 조건 (peak × (1 - trail_pct) 이탈)
  4. MA 이탈 → 다음날 시가 청산
"""
from dataclasses import dataclass

import pandas as pd

from src.backtest.costs import make_cost_model


@dataclass
class SwingSignal:
    symbol: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp   # signal_date + 1 거래일
    stop_distance: float       # entry_price - stop_price


def run_swing_backtest(
    signals: list[SwingSignal],
    price_data: dict[str, pd.DataFrame],
    spy_series: pd.Series,
    cfg: dict,
    strategy_cfg_key: str,
) -> tuple[pd.Series, list[dict]]:
    s_cfg    = cfg.get(strategy_cfg_key, {})
    cap      = cfg["backtest"]["initial_capital_usd"]
    risk_pct = cfg["risk"]["risk_per_trade_pct"] / 100
    slip     = cfg["risk"]["slippage_pct"] / 100
    max_pos  = s_cfg.get("max_positions", cfg["risk"]["max_positions"])
    trail    = s_cfg.get("trail_pct", 0.20)
    ma_exit  = s_cfg.get("trail_ma_period", 50)
    ma200_p  = s_cfg.get("spy_ma200_period", 200)
    cost     = make_cost_model(cfg)

    spy_ma200 = spy_series.rolling(ma200_p).mean()
    regime_ok = set(spy_series.index[spy_series > spy_ma200])

    spy_df = price_data.get("SPY", next(iter(price_data.values())))
    all_dates = list(spy_df.index)

    # MA exit 캐시
    ma_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        if ma_exit > 0:
            ma_cache[sym] = df["close"].rolling(ma_exit).mean()

    signals_by_date: dict[pd.Timestamp, list[SwingSignal]] = {}
    for s in signals:
        signals_by_date.setdefault(s.entry_date, []).append(s)

    cash = float(cap)
    positions: dict[str, dict] = {}
    ma_exit_next: set[str] = set()
    trades: list[dict] = []
    eq: list[dict] = []

    for date in all_dates:
        # MA 이탈 예약 → 시가 청산
        for sym in list(ma_exit_next):
            ma_exit_next.discard(sym)
            if sym not in positions or date not in price_data[sym].index:
                continue
            px = price_data[sym].loc[date, "open"] * (1 - slip)
            pos = positions.pop(sym)
            sell_c = cost.sell_cost(px * pos["shares"])
            pnl = (px - pos["entry_price"]) * pos["shares"] - sell_c
            cash += px * pos["shares"] - sell_c
            trades.append({**pos, "exit_date": date, "exit_price": px,
                           "exit_reason": "ma_exit", "pnl": pnl})

        # 보유 포지션 손절·트레일 체크
        for sym in list(positions):
            if date not in price_data[sym].index:
                continue
            bar = price_data[sym].loc[date]
            pos = positions[sym]
            stop = pos["stop"]

            # 갭다운 손절
            if bar["open"] <= stop:
                px = bar["open"] * (1 - slip)
                sell_c = cost.sell_cost(px * pos["shares"])
                pnl = (px - pos["entry_price"]) * pos["shares"] - sell_c
                cash += px * pos["shares"] - sell_c
                trades.append({**pos, "exit_date": date, "exit_price": px,
                               "exit_reason": "stop_gap", "pnl": pnl})
                del positions[sym]
                continue
            # 장중 손절
            if bar["low"] <= stop:
                px = stop * (1 - slip)
                sell_c = cost.sell_cost(px * pos["shares"])
                pnl = (px - pos["entry_price"]) * pos["shares"] - sell_c
                cash += px * pos["shares"] - sell_c
                trades.append({**pos, "exit_date": date, "exit_price": px,
                               "exit_reason": "stop", "pnl": pnl})
                del positions[sym]
                continue

            # 피크 업데이트
            pos["peak"] = max(pos["peak"], bar["close"])
            trail_stop = pos["peak"] * (1 - trail)
            if bar["close"] < trail_stop:
                px = bar["close"] * (1 - slip)
                sell_c = cost.sell_cost(px * pos["shares"])
                pnl = (px - pos["entry_price"]) * pos["shares"] - sell_c
                cash += px * pos["shares"] - sell_c
                trades.append({**pos, "exit_date": date, "exit_price": px,
                               "exit_reason": "trail", "pnl": pnl})
                del positions[sym]
                continue

            # MA 이탈 예약
            if ma_exit > 0 and sym in ma_cache and date in ma_cache[sym].index:
                ma_val = ma_cache[sym].loc[date]
                if not pd.isna(ma_val) and bar["close"] < ma_val:
                    ma_exit_next.add(sym)

        # 신규 진입
        if date in regime_ok and date in signals_by_date:
            cands = sorted(signals_by_date[date], key=lambda s: s.stop_distance)
            for sig in cands:
                if len(positions) >= max_pos:
                    break
                if sig.symbol in positions:
                    continue
                if date not in price_data[sig.symbol].index:
                    continue
                bar = price_data[sig.symbol].loc[date]
                entry_px = bar["open"] * (1 + slip)
                stop_px  = entry_px - sig.stop_distance
                if stop_px <= 0 or sig.stop_distance <= 0:
                    continue
                risk_amt = cap * risk_pct
                shares = risk_amt / sig.stop_distance
                cost_est = cost.buy_cost(shares * entry_px)
                if shares * entry_px + cost_est > cash * 0.95:
                    shares = (cash * 0.95) / (entry_px * (1 + cost.commission_pct))
                if shares < 0.01:
                    continue
                buy_c = cost.buy_cost(shares * entry_px)
                cash -= shares * entry_px + buy_c
                positions[sig.symbol] = {
                    "symbol": sig.symbol,
                    "entry_date": date,
                    "entry_price": entry_px,
                    "stop": stop_px,
                    "stop_distance": sig.stop_distance,
                    "shares": shares,
                    "peak": entry_px,
                }

        # 일별 자산 평가
        pos_val = sum(
            positions[sym]["shares"] * price_data[sym].loc[date, "close"]
            for sym in positions if date in price_data[sym].index
        )
        eq.append({"date": date, "equity": cash + pos_val})

    equity = pd.DataFrame(eq).set_index("date")["equity"]
    return equity, trades
