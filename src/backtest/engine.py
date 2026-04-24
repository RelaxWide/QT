"""
범용 백테스트 엔진 — Phase 1/2/3/4 + HighWR MA Bounce

Signal.targets:        달러 기준 부분 청산 목표가 리스트 (오름차순)
Signal.partial_weights: 각 목표 도달 시 청산 비중 (합 < 1.0 → 나머지는 트레일)
trail_data:            {symbol: pd.Series} — 트레일 기준선 (donchian lower 또는 tenkan)
                       현재 bar의 close < trail_val → 잔여 포지션 청산

비용 처리:
  - 수수료: 매수/매도 각 commission_pct% (config.cost.commission_pct)
  - 양도소득세: 연말 연간 순이익 공제 후 22% (config.cost.*)
  - trade 통계(PF, WR, 평균R)는 수수료 차감 전 총손익 기준
  - equity curve는 수수료+세금 반영 후 순손익 기준
"""
from dataclasses import dataclass
import pandas as pd

from src.strategy.breakout_pullback import Signal
from src.backtest.costs import make_cost_model


@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_initial: float
    stop_current: float
    targets: list
    partial_weights: list
    shares_total: float
    shares_remaining: float
    realized_pnl: float = 0.0
    targets_hit: int = 0


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_initial: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl: float


@dataclass
class BacktestResult:
    trades: list
    equity_curve: pd.Series
    initial_capital: float


def run_backtest(
    signals: list[Signal],
    price_data: dict[str, pd.DataFrame],
    regime: pd.DataFrame,
    config: dict,
    trail_data: dict[str, pd.Series] | None = None,
) -> BacktestResult:
    risk_cfg = config["risk"]
    initial_capital: float = config["backtest"]["initial_capital_usd"]
    risk_pct:  float = risk_cfg["risk_per_trade_pct"] / 100
    max_pos:   int   = risk_cfg["max_positions"]
    slippage:  float = risk_cfg["slippage_pct"] / 100

    cap_cfg           = config.get("capital_mgmt", {})
    max_entries_day   = cap_cfg.get("max_new_entries_day", 999)
    max_daily_pct     = cap_cfg.get("max_daily_invest_pct", 100) / 100
    target_invest_pct = cap_cfg.get("target_invested_pct", 100) / 100

    cost = make_cost_model(config)

    if trail_data is None:
        p1 = config.get("phase1_breakout_pullback", {})
        trail_period = p1.get("trail_donchian_period", 10)
        trail_data = {
            sym: df["low"].rolling(trail_period).min().shift(1)
            for sym, df in price_data.items()
        }

    spy_df     = price_data.get("SPY", next(iter(price_data.values())))
    all_dates  = list(spy_df.index)

    cash = initial_capital
    positions: dict[str, Position] = {}
    trades: list[ClosedTrade] = []
    equity_records: list[dict] = []

    signals_by_date: dict[pd.Timestamp, list[Signal]] = {}
    for sig in signals:
        signals_by_date.setdefault(sig.entry_date, []).append(sig)

    for date in all_dates:
        # ── 1. Exit checks ────────────────────────────────────────────────
        to_close: list[tuple] = []

        for sym, pos in positions.items():
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue

            loc = df_sym.index.get_loc(date)
            bar = df_sym.iloc[loc]

            # 스톱 (갭다운 우선)
            if bar["open"] <= pos.stop_current:
                exit_px = bar["open"] * (1 - slippage)
                reason  = "stop_gap" if pos.targets_hit == 0 else f"stop_gap_t{pos.targets_hit}"
                to_close.append((sym, exit_px, pos.shares_remaining, reason))
                continue
            if bar["low"] <= pos.stop_current:
                exit_px = pos.stop_current * (1 - slippage)
                reason  = "stop" if pos.targets_hit == 0 else f"stop_t{pos.targets_hit}"
                to_close.append((sym, exit_px, pos.shares_remaining, reason))
                continue

            # 목표가 부분 청산
            if pos.targets_hit < len(pos.targets):
                target_price = pos.targets[pos.targets_hit]
                hit_price    = bar["open"] if bar["open"] >= target_price else (
                               target_price if bar["high"] >= target_price else None)
                if hit_price is not None:
                    partial_px     = hit_price * (1 - slippage)
                    partial_shares = pos.shares_remaining * pos.partial_weights[pos.targets_hit]
                    gross_pnl      = (partial_px - pos.entry_price) * partial_shares
                    sell_comm      = cost.sell_cost(partial_px * partial_shares)
                    pos.realized_pnl     += gross_pnl
                    pos.shares_remaining -= partial_shares
                    cash += partial_px * partial_shares - sell_comm
                    pos.targets_hit += 1
                    if pos.targets_hit == 1:
                        pos.stop_current = pos.entry_price
                    continue

            # 트레일
            if pos.targets_hit >= len(pos.targets):
                tl = trail_data.get(sym)
                if tl is not None and date in tl.index:
                    trail_val = tl.at[date]
                    if not pd.isna(trail_val) and bar["low"] <= trail_val:
                        exit_px = bar["low"] * (1 - slippage)
                        to_close.append((sym, exit_px, pos.shares_remaining, "trail"))

        for sym, exit_px, exit_shares, reason in to_close:
            pos        = positions.pop(sym)
            final_pnl  = (exit_px - pos.entry_price) * exit_shares
            total_pnl  = pos.realized_pnl + final_pnl
            init_risk  = (pos.entry_price - pos.stop_initial) * pos.shares_total
            r_mult     = total_pnl / init_risk if init_risk > 0 else 0.0
            sell_comm  = cost.sell_cost(exit_px * exit_shares)
            cash      += exit_px * exit_shares - sell_comm

            trades.append(ClosedTrade(
                symbol=sym,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                stop_initial=pos.stop_initial,
                exit_date=date,
                exit_price=pos.entry_price + total_pnl / pos.shares_total,
                exit_reason=reason,
                r_multiple=r_mult,
                pnl=total_pnl,
            ))

        # ── 2. Regime ─────────────────────────────────────────────────────
        regime_ok   = True
        size_factor = 1.0
        if date in regime.index:
            regime_ok   = bool(regime.at[date, "trade_ok"])
            size_factor = float(regime.at[date, "size_factor"])

        # ── 3. New positions ──────────────────────────────────────────────
        if regime_ok and len(positions) < max_pos:
            pos_val_now = sum(
                price_data[s].at[date, "close"] * p.shares_remaining
                for s, p in positions.items()
                if s in price_data and date in price_data[s].index
            )
            equity_now    = cash + pos_val_now
            daily_budget  = equity_now * max_daily_pct
            daily_spent   = 0.0
            entries_today = 0

            for sig in signals_by_date.get(date, []):
                if len(positions) >= max_pos or entries_today >= max_entries_day:
                    break
                if sig.symbol in positions or sig.symbol not in price_data:
                    continue

                cur_invested = pos_val_now + daily_spent
                if equity_now > 0 and cur_invested / equity_now >= target_invest_pct:
                    break

                entry_px  = sig.entry_price * (1 + slippage)
                r         = entry_px - sig.stop
                if r <= 0:
                    continue

                risk_amt  = initial_capital * risk_pct * size_factor
                shares    = risk_amt / r
                trade_val = entry_px * shares
                buy_comm  = cost.buy_cost(trade_val)
                total_cost = trade_val + buy_comm
                if total_cost > cash * 0.95:
                    shares     = (cash * 0.95) / (entry_px * (1 + cost.commission_pct))
                    trade_val  = entry_px * shares
                    buy_comm   = cost.buy_cost(trade_val)
                    total_cost = trade_val + buy_comm
                if shares < 0.01:
                    continue

                if daily_spent + trade_val > daily_budget:
                    continue

                cash          -= total_cost
                daily_spent   += trade_val
                entries_today += 1
                positions[sig.symbol] = Position(
                    symbol=sig.symbol,
                    entry_date=date,
                    entry_price=entry_px,
                    stop_initial=sig.stop,
                    stop_current=sig.stop,
                    targets=list(sig.targets),
                    partial_weights=list(sig.partial_weights),
                    shares_total=shares,
                    shares_remaining=shares,
                )

        # ── 4. Equity ─────────────────────────────────────────────────────
        pos_value = sum(
            price_data[s].at[date, "close"] * p.shares_remaining
            for s, p in positions.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
    )
