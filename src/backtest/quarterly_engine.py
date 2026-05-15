"""
KR 분기 리밸런싱 백테스트 엔진.

설계 원칙:
  - haa_engine 패턴 차용 (target_value 기반 재조정)
  - FundamentalSignal 입력 (kw_* 전략 출력)
  - KR 호가단위 라운딩 (tick_size_kospi)
  - 거래정지 가드 (open <= 0 → 다음 영업일까지 보류)
  - equity 평가 시 데이터 누락 → df.close.asof(date) ffill (clenow_engine 5/15 fix 와 동일)
  - cost_model (KR fee_model: 매수 0.015% + 매도 0.18% + 농특세 0.023%)
  - 옵션: 레짐 필터 (use_regime=True, regime_index 비교)
"""
from __future__ import annotations

import pandas as pd

from src.backtest.costs import make_cost_model
from src.markets.tick_size import round_buy_to_tick, round_sell_to_tick
from src.strategy._kw_common import FundamentalSignal


def _position_value_ffill(
    holdings: dict[str, float],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> float:
    """보유 평가 — 종목 데이터 누락 시 직전 close ffill."""
    total = 0.0
    for sym, sh in holdings.items():
        df = price_data.get(sym)
        if df is None or df.empty:
            continue
        if date in df.index:
            c = df.loc[date, "close"]
        else:
            c = df["close"].asof(date)
        if pd.notna(c) and c > 0:
            total += sh * float(c)
    return total


def _next_trading_date(
    price_data: dict[str, pd.DataFrame],
    after: pd.Timestamp,
) -> pd.Timestamp | None:
    dates: set[pd.Timestamp] = set()
    for df in price_data.values():
        dates.update(df.index[df.index > after].tolist())
    return min(dates) if dates else None


def run_quarterly_backtest(
    signals: list[FundamentalSignal],
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
    market: str = "kr",
) -> tuple[pd.Series, pd.DataFrame]:
    """KR 분기 리밸런싱 백테스트.

    Returns:
        equity: 일별 자산 시리즈 (KRW)
        trades: 거래 기록 DataFrame
    """
    initial_capital = float(cfg["backtest"]["initial_capital_usd"])
    slippage = float(cfg["risk"].get("slippage_pct", 0.5)) / 100   # KR 은 기본 0.5%
    cost_model = make_cost_model(cfg)

    # signal date → exec date (다음 영업일)
    exec_map: dict[pd.Timestamp, FundamentalSignal] = {}
    for sig in signals:
        # 신호 일 자체가 영업일 보정된 거라 그대로 사용, 없으면 다음 영업일
        if sig.date in price_data.get(next(iter(price_data)), pd.DataFrame()).index:
            exec_map[sig.date] = sig
        else:
            nxt = _next_trading_date(price_data, sig.date)
            if nxt is not None:
                exec_map[nxt] = sig

    all_dates = sorted({d for df in price_data.values() for d in df.index})
    if exec_map:
        first_exec = min(exec_map)
        all_dates = [d for d in all_dates if d >= first_exec]

    cash = initial_capital
    holdings: dict[str, float] = {}
    cost_basis: dict[str, float] = {}
    equity_records: list[dict] = []
    trade_records: list[dict] = []

    for date in all_dates:
        if date in exec_map:
            sig = exec_map[date]

            # 현재 포트폴리오 가치 (open 가격 기준, ffill)
            pv_open = 0.0
            for sym, sh in holdings.items():
                df = price_data.get(sym)
                if df is None:
                    continue
                if date in df.index:
                    op = df.loc[date, "open"]
                else:
                    op = df["open"].asof(date)
                if pd.notna(op) and op > 0:
                    pv_open += sh * float(op)
            portfolio_value = cash + pv_open

            # target value per sym
            target_values = {sym: portfolio_value * w for sym, w in sig.weights.items()}

            # 1) 매도 — 탈락 종목 or 초과 보유
            for sym, sh in list(holdings.items()):
                df = price_data.get(sym)
                if df is None or date not in df.index:
                    continue
                open_px = df.loc[date, "open"]
                if pd.isna(open_px) or open_px <= 0:
                    continue   # 거래정지/누락 — 보류
                cur_val = sh * open_px * (1 - slippage)
                tgt_val = target_values.get(sym, 0.0)
                if cur_val <= tgt_val:
                    continue
                sell_value = cur_val - tgt_val
                sell_px = round_sell_to_tick(open_px * (1 - slippage), market)
                if sell_px <= 0:
                    continue
                sell_shares = min(sh, sell_value / sell_px)
                if sell_shares <= 1e-9:
                    continue
                proceeds = sell_px * sell_shares
                fee = cost_model.sell_cost(proceeds)
                cash += proceeds - fee
                holdings[sym] = sh - sell_shares
                trade_records.append({
                    "date": date, "symbol": sym, "side": "sell",
                    "price": sell_px, "shares": sell_shares,
                    "value": proceeds, "fee": fee,
                    "strategy": sig.strategy,
                })
                if holdings[sym] <= 1e-9:
                    holdings.pop(sym, None)
                    cost_basis.pop(sym, None)

            # 2) 매수 — 타겟 미만이면 추가
            for sym, target_value in target_values.items():
                df = price_data.get(sym)
                if df is None or date not in df.index:
                    continue
                open_px = df.loc[date, "open"]
                if pd.isna(open_px) or open_px <= 0:
                    continue
                current_sh = holdings.get(sym, 0.0)
                current_val = current_sh * open_px * (1 + slippage)
                buy_value = min(max(target_value - current_val, 0.0), cash)
                if buy_value <= 1000.0:    # 1000원 미만 무시
                    continue
                buy_px = round_buy_to_tick(open_px * (1 + slippage), market)
                if buy_px <= 0:
                    continue
                fee = cost_model.buy_cost(buy_value)
                avail = max(buy_value - fee, 0.0)
                buy_shares = int(avail / buy_px)   # KR 은 1주 단위 정수
                if buy_shares <= 0:
                    continue
                actual_cost = buy_shares * buy_px
                actual_fee  = cost_model.buy_cost(actual_cost)
                cash -= actual_cost + actual_fee
                holdings[sym] = current_sh + buy_shares
                cost_basis[sym] = buy_px
                trade_records.append({
                    "date": date, "symbol": sym, "side": "buy",
                    "price": buy_px, "shares": buy_shares,
                    "value": actual_cost, "fee": actual_fee,
                    "strategy": sig.strategy,
                })

        # 일별 equity (ffill)
        equity_records.append({
            "date": date,
            "equity": cash + _position_value_ffill(holdings, price_data, date),
        })

    equity = pd.DataFrame(equity_records).set_index("date")["equity"]
    trades = pd.DataFrame(trade_records)
    return equity, trades


def compute_quarterly_metrics(equity: pd.Series, initial_capital: float) -> dict:
    """clenow_engine.compute_clenow_metrics 와 동일 메트릭 세트."""
    import numpy as np

    daily_ret = equity.pct_change().dropna()
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital
    n_years   = len(equity) / 252
    cagr      = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    running_max = equity.cummax()
    drawdown    = (equity - running_max) / running_max
    max_dd      = drawdown.min()

    dd_dur = max_dd_dur = 0
    peak = equity.iloc[0]
    for val in equity:
        if val >= peak:
            peak = val
            dd_dur = 0
        else:
            dd_dur += 1
            max_dd_dur = max(max_dd_dur, dd_dur)

    sharpe  = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    down_r  = daily_ret[daily_ret < 0].std()
    sortino = daily_ret.mean() / down_r * np.sqrt(252) if down_r > 0 else 0
    calmar  = cagr / abs(max_dd) if max_dd != 0 else 0

    monthly_ret = equity.resample("ME").last().pct_change().dropna()
    monthly_wr  = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0

    return {
        "total_return_pct":  round(total_ret * 100, 2),
        "cagr_pct":          round(cagr * 100, 2),
        "max_drawdown_pct":  round(max_dd * 100, 2),
        "max_drawdown_days": max_dd_dur,
        "sharpe":            round(sharpe, 4),
        "sortino":           round(sortino, 4),
        "calmar":            round(calmar, 4),
        "monthly_win_rate":  round(monthly_wr, 4),
    }
