"""
Backtest engine for monthly multi-asset HAA allocations.
"""
from __future__ import annotations

import pandas as pd

from src.backtest.costs import make_cost_model
from src.strategy.haa import HAASignal


def _next_trading_date(price_data: dict[str, pd.DataFrame], after: pd.Timestamp) -> pd.Timestamp | None:
    dates: set[pd.Timestamp] = set()
    for df in price_data.values():
        dates.update(df.index[df.index > after].tolist())
    return min(dates) if dates else None


def run_haa_backtest(
    signals: list[HAASignal],
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, pd.DataFrame]:
    initial_capital = float(cfg["backtest"]["initial_capital_usd"])
    slippage = float(cfg["risk"].get("slippage_pct", 0.0)) / 100
    cost_model = make_cost_model(cfg)

    exec_map: dict[pd.Timestamp, HAASignal] = {}
    for sig in signals:
        exec_date = _next_trading_date(price_data, sig.date)
        if exec_date is not None:
            exec_map[exec_date] = sig

    all_dates = sorted({date for df in price_data.values() for date in df.index})
    if exec_map:
        first_exec = min(exec_map)
        all_dates = [date for date in all_dates if date >= first_exec]
    cash = initial_capital
    holdings: dict[str, float] = {}
    cost_basis: dict[str, float] = {}
    equity_records: list[dict] = []
    trade_records: list[dict] = []

    for date in all_dates:
        if date in exec_map:
            sig = exec_map[date]
            portfolio_value = cash + _position_value(holdings, price_data, date)

            # Sell assets that are no longer targeted or need downsizing.
            target_values = {sym: portfolio_value * weight for sym, weight in sig.weights.items()}

            for sym, shares in list(holdings.items()):
                df = price_data.get(sym)
                if df is None or date not in df.index:
                    continue
                current_value = shares * df.loc[date, "open"] * (1 - slippage)
                target_value = target_values.get(sym, 0.0)
                if current_value <= target_value:
                    continue
                sell_value = current_value - target_value
                sell_shares = min(shares, sell_value / (df.loc[date, "open"] * (1 - slippage)))
                sell_px = df.loc[date, "open"] * (1 - slippage)
                proceeds = sell_px * sell_shares
                fee = cost_model.sell_cost(proceeds)
                cash += proceeds - fee
                holdings[sym] = shares - sell_shares
                mode = getattr(sig, "mode", "rotation")
                trade_records.append(
                    {
                        "date": date,
                        "symbol": sym,
                        "side": "sell",
                        "price": sell_px,
                        "shares": sell_shares,
                        "value": proceeds,
                        "fee": fee,
                        "mode": mode,
                    }
                )
                if holdings[sym] <= 1e-9:
                    holdings.pop(sym, None)
                    cost_basis.pop(sym, None)

            # Buy assets below target after sales create cash.
            for sym, target_value in target_values.items():
                df = price_data.get(sym)
                if df is None or date not in df.index:
                    continue
                open_px = df.loc[date, "open"]
                current_shares = holdings.get(sym, 0.0)
                current_value = current_shares * open_px * (1 + slippage)
                buy_value = min(max(target_value - current_value, 0.0), cash)
                if buy_value <= 1.0:
                    continue
                buy_px = open_px * (1 + slippage)
                fee = cost_model.buy_cost(buy_value)
                available = max(buy_value - fee, 0.0)
                buy_shares = available / buy_px
                cash -= buy_shares * buy_px + fee
                holdings[sym] = current_shares + buy_shares
                cost_basis[sym] = buy_px
                mode = getattr(sig, "mode", "rotation")
                trade_records.append(
                    {
                        "date": date,
                        "symbol": sym,
                        "side": "buy",
                        "price": buy_px,
                        "shares": buy_shares,
                        "value": buy_shares * buy_px,
                        "fee": fee,
                        "mode": mode,
                    }
                )

        equity_records.append(
            {
                "date": date,
                "equity": cash + _position_value(holdings, price_data, date),
            }
        )

    equity = pd.DataFrame(equity_records).set_index("date")["equity"]
    trades = pd.DataFrame(trade_records)
    return equity, trades


def _position_value(
    holdings: dict[str, float],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> float:
    value = 0.0
    for sym, shares in holdings.items():
        df = price_data.get(sym)
        if df is not None and date in df.index:
            value += shares * df.loc[date, "close"]
    return value
