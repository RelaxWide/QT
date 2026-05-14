"""
Phase 4-v2 백테스트 엔진

engine.py 기반이나 trail 로직을 cloud_mid 이탈 청산으로 교체:
  - 매일 cloud_mid = (senkou_a + senkou_b) / 2 계산
  - bar.close < cloud_mid → 다음 bar 시가 청산 (cloud_mid_exit)
  - time stop: max_hold_bars 초과 시 청산

slope_method 두 버전('reg10', 'avg5') config 토글로 구분 가능하도록
run_phase4_v2.py에서 config['phase4_v2']['slope_method']를 바꿔 두 번 실행.
"""
from dataclasses import dataclass
import pandas as pd

from src.strategy.breakout_pullback import Signal
from src.backtest.costs import make_cost_model
from src.backtest.engine import ClosedTrade, BacktestResult


@dataclass
class AnticipPos:
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
    _pending_reason: str = "cloud_exit"
    cloud_mid_exit_pending: bool = False  # 전날 종가 이탈 → 오늘 시가 청산
    _stop_pending: bool = False           # True면 손절, False면 구름 이탈
    touch_bar: int = -1                   # 구름 상단 터치 확인된 봉 index (-1=미터치)
    bars_since_touch: int = 0             # 터치 후 경과 봉 수


def run_anticipatory_backtest(
    signals: list[Signal],
    price_data: dict[str, pd.DataFrame],
    cloud_data: dict[str, pd.DataFrame],  # {sym: df with senkou_a, senkou_b columns}
    regime: pd.DataFrame,
    config: dict,
    max_hold_bars: int = 3,              # 구름 터치 후 최대 보유 봉 수
    max_total_bars: int = 15,            # 터치 없이 전체 타임아웃 (안전망)
    cloud_exit_level: str = "bottom",    # "mid" = cloud_mid, "bottom" = senkou_b
    max_touch_fail_bars: int = 0,
    min_touch_bounce_r: float = 0.0,
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

    spy_df    = price_data.get("SPY", next(iter(price_data.values())))
    all_dates = list(spy_df.index)

    cash = initial_capital
    positions: dict[str, AnticipPos] = {}
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

            # pending 청산 → 오늘 시가 청산 (종가 기반 손절 또는 구름 이탈)
            if pos.cloud_mid_exit_pending:
                exit_px = bar["open"] * (1 - slippage)
                reason  = getattr(pos, "_pending_reason", None)
                if not reason:
                    reason = ("stop" if getattr(pos, "_stop_pending", False) else "cloud_exit")
                if pos.targets_hit > 0:
                    reason += f"_t{pos.targets_hit}"
                to_close.append((sym, exit_px, pos.shares_remaining, reason))
                continue

            # 전체 안전망 타임스탑 (구름 미도달 포함)
            bars_held = loc - df_sym.index.get_loc(pos.entry_date)
            if bars_held >= max_total_bars:
                exit_px = bar["open"] * (1 - slippage)
                to_close.append((sym, exit_px, pos.shares_remaining, "time_stop"))
                continue

            # 갭다운 보호 (시가가 손절선 아래로 시작 — 장 시작부터 손실 확정)
            if bar["open"] <= pos.stop_current:
                exit_px = bar["open"] * (1 - slippage)
                reason  = "stop_gap" if pos.targets_hit == 0 else f"stop_gap_t{pos.targets_hit}"
                to_close.append((sym, exit_px, pos.shares_remaining, reason))
                continue
            # 종가 기반 손절 — 종가가 손절선 아래면 다음날 시가 청산 pending
            # (장중 터치는 지지 시도로 판단, 종가 확정 시에만 포기)
            if bar["close"] <= pos.stop_current and not pos.cloud_mid_exit_pending:
                pos.cloud_mid_exit_pending = True
                # exit reason은 나중에 구별하기 위해 별도 플래그로 처리
                pos._stop_pending = True
                pos._pending_reason = "stop"

            # 목표가 부분 청산
            if pos.targets_hit < len(pos.targets):
                target_price = pos.targets[pos.targets_hit]
                hit_price = bar["open"] if bar["open"] >= target_price else (
                            target_price if bar["high"] >= target_price else None)
                if hit_price is not None:
                    partial_px     = hit_price * (1 - slippage)
                    partial_shares = pos.shares_total * pos.partial_weights[pos.targets_hit]
                    gross_pnl      = (partial_px - pos.entry_price) * partial_shares
                    sell_comm      = cost.sell_cost(partial_px * partial_shares)
                    pos.realized_pnl     += gross_pnl
                    pos.shares_remaining -= partial_shares
                    cash += partial_px * partial_shares - sell_comm
                    pos.targets_hit += 1
                    if pos.targets_hit == 1:
                        pos.stop_current = pos.entry_price
                    continue

            # 구름 터치 감지 + 이탈 체크
            cd = cloud_data.get(sym)
            if cd is not None and date in cd.index:
                sa = cd.at[date, "senkou_a"]
                sb = cd.at[date, "senkou_b"]
                if not pd.isna(sa) and not pd.isna(sb):
                    cloud_top = max(sa, sb)

                    # 구름 상단 터치 감지 (종가가 구름 상단 근처에 닿으면)
                    if pos.touch_bar < 0 and bar["close"] <= cloud_top * 1.005:
                        pos.touch_bar = loc
                        pos.bars_since_touch = 0
                    if pos.touch_bar >= 0:
                        pos.bars_since_touch = loc - pos.touch_bar
                        if max_touch_fail_bars > 0 and pos.bars_since_touch >= max_touch_fail_bars:
                            min_bounce_px = pos.entry_price + min_touch_bounce_r * (pos.entry_price - pos.stop_initial)
                            if bar["close"] < min_bounce_px and not pos.cloud_mid_exit_pending:
                                pos.cloud_mid_exit_pending = True
                                pos._pending_reason = "touch_fail"

                    # 구름 하단 이탈 → 청산 (반등 실패)
                    # 터치 후 N봉 강제청산 제거 — 반등 중인 포지션 조기청산 방지
                    if cloud_exit_level == "top":
                        threshold = cloud_top
                    elif cloud_exit_level == "mid":
                        threshold = (sa + sb) / 2
                    else:
                        threshold = min(sa, sb)
                    if bar["close"] < threshold and not pos.cloud_mid_exit_pending:
                        pos.cloud_mid_exit_pending = True
                        pos._pending_reason = "cloud_exit"

        for sym, exit_px, exit_shares, reason in to_close:
            pos        = positions.pop(sym)
            final_pnl  = (exit_px - pos.entry_price) * exit_shares
            total_pnl  = pos.realized_pnl + final_pnl
            init_risk  = (pos.entry_price - pos.stop_initial) * pos.shares_total
            r_mult     = total_pnl / init_risk if init_risk > 0 else 0.0
            sell_comm  = cost.sell_cost(exit_px * exit_shares)
            cash      += exit_px * exit_shares - sell_comm

            trades.append(ClosedTrade(
                symbol      = sym,
                entry_date  = pos.entry_date,
                entry_price = pos.entry_price,
                stop_initial= pos.stop_initial,
                exit_date   = date,
                exit_price  = pos.entry_price + total_pnl / pos.shares_total,
                exit_reason = reason,
                r_multiple  = r_mult,
                pnl         = total_pnl,
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

                risk_amt   = initial_capital * risk_pct * size_factor
                shares     = risk_amt / r
                trade_val  = entry_px * shares
                buy_comm   = cost.buy_cost(trade_val)
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
                positions[sig.symbol] = AnticipPos(
                    symbol          = sig.symbol,
                    entry_date      = date,
                    entry_price     = entry_px,
                    stop_initial    = sig.stop,
                    stop_current    = sig.stop,
                    targets         = list(sig.targets),
                    partial_weights = list(sig.partial_weights),
                    shares_total    = shares,
                    shares_remaining= shares,
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
        trades         = trades,
        equity_curve   = equity_curve,
        initial_capital= initial_capital,
    )
