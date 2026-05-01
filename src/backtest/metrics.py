from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from src.backtest.engine import BacktestResult


def compute_metrics(result: BacktestResult) -> dict:
    trades = result.trades
    eq = result.equity_curve

    if not trades:
        return {"error": "no trades"}

    pnls = [t.pnl for t in trades]
    r_mults = [t.r_multiple for t in trades]
    wins = [r for r in r_mults if r > 0]
    losses = [r for r in r_mults if r <= 0]

    win_rate = len(wins) / len(r_mults) if r_mults else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = (
        sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
    )
    avg_r = np.mean(r_mults)

    # Drawdown
    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    max_dd = drawdown.min()

    dd_start = dd_end = None
    in_dd = False
    peak = eq.iloc[0]
    dd_dur = 0
    max_dd_dur = 0
    for val in eq:
        if val >= peak:
            peak = val
            if in_dd:
                in_dd = False
                dd_dur = 0
        else:
            in_dd = True
            dd_dur += 1
            max_dd_dur = max(max_dd_dur, dd_dur)

    # Sharpe (annualised, daily returns)
    daily_ret = eq.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    sortino_denom = daily_ret[daily_ret < 0].std()
    sortino = (daily_ret.mean() / sortino_denom * np.sqrt(252)) if sortino_denom > 0 else 0

    total_return = (eq.iloc[-1] - result.initial_capital) / result.initial_capital
    n_years = len(eq) / 252
    cagr = (eq.iloc[-1] / result.initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    exit_reasons = pd.Series([t.exit_reason for t in trades]).value_counts().to_dict()

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "avg_r": round(avg_r, 4),
        "avg_win_r": round(avg_win, 4),
        "avg_loss_r": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_days": max_dd_dur,
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "exit_reasons": exit_reasons,
    }


def compute_rotation_metrics(equity: pd.Series, initial_capital: float) -> dict:
    daily_ret = equity.pct_change().dropna()
    n_years = len(equity) / 252
    cagr = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = drawdown.min()

    dd_dur = max_dd_dur = 0
    peak = equity.iloc[0]
    for val in equity:
        if val >= peak:
            peak = val
            dd_dur = 0
        else:
            dd_dur += 1
            max_dd_dur = max(max_dd_dur, dd_dur)

    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    down_r = daily_ret[daily_ret < 0].std()
    sortino = daily_ret.mean() / down_r * np.sqrt(252) if down_r > 0 else 0
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    monthly_ret = equity.resample("ME").last().pct_change().dropna()
    monthly_wr = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0

    return {
        "total_return_pct":     round((equity.iloc[-1] - initial_capital) / initial_capital * 100, 2),
        "cagr_pct":             round(cagr * 100, 2),
        "max_drawdown_pct":     round(max_dd * 100, 2),
        "max_drawdown_days":    max_dd_dur,
        "sharpe":               round(sharpe, 4),
        "sortino":              round(sortino, 4),
        "calmar":               round(calmar, 4),
        "monthly_win_rate":     round(monthly_wr, 4),
        "monthly_observations": len(monthly_ret),
    }


def save_report(metrics: dict, result: BacktestResult, output_dir: str = "backtest_results", prefix: str = "phase1") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Trade log CSV ──
    trades_df = pd.DataFrame([
        {
            "symbol": t.symbol,
            "entry_date": t.entry_date,
            "entry_price": round(t.entry_price, 4),
            "stop": round(t.stop_initial, 4),
            "exit_date": t.exit_date,
            "exit_price": round(t.exit_price, 4),
            "exit_reason": t.exit_reason,
            "r_multiple": round(t.r_multiple, 4),
            "pnl": round(t.pnl, 2),
        }
        for t in result.trades
    ])
    trades_df.to_csv(out / f"{prefix}_trades.csv", index=False)

    # ── Equity curve CSV ──
    result.equity_curve.to_csv(out / f"{prefix}_equity.csv", header=True)

    # ── Equity curve chart ──
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})

    eq = result.equity_curve
    axes[0].plot(eq.index, eq.values, linewidth=1.2, color="#2196F3")
    axes[0].axhline(result.initial_capital, color="gray", linestyle="--", linewidth=0.8)
    axes[0].set_title("Phase 1: Breakout Pullback — Equity Curve")
    axes[0].set_ylabel("Portfolio Value ($)")
    axes[0].grid(alpha=0.3)

    dd = (eq - eq.cummax()) / eq.cummax() * 100
    axes[1].fill_between(dd.index, dd.values, 0, color="#F44336", alpha=0.5)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out / f"{prefix}_equity.png", dpi=150)
    plt.close()

    # ── Markdown report ──
    exit_table = "\n".join(
        f"| {k} | {v} |" for k, v in metrics.get("exit_reasons", {}).items()
    )
    report = f"""# Phase 1: Breakout Pullback — 백테스트 결과

## 핵심 지표

| 지표 | 값 |
|---|---|
| 총 트레이드 수 | {metrics['total_trades']} |
| 승률 | {metrics['win_rate']:.1%} |
| 평균 R | {metrics['avg_r']:.2f}R |
| 평균 승리 R | {metrics['avg_win_r']:.2f}R |
| 평균 손실 R | {metrics['avg_loss_r']:.2f}R |
| Profit Factor | {metrics['profit_factor']:.2f} |
| 총 수익률 | {metrics['total_return_pct']:.1f}% |
| 최대 낙폭 | {metrics['max_drawdown_pct']:.1f}% |
| 최대 낙폭 지속 (일) | {metrics['max_drawdown_days']} |
| Sharpe | {metrics['sharpe']:.2f} |
| Sortino | {metrics['sortino']:.2f} |
| Calmar | {metrics['calmar']:.2f} |

## Phase 게이트 체크

| 기준 | 최소 | 결과 | 통과 |
|---|---|---|---|
| 샘플 수 | 100 | {metrics['total_trades']} | {'✅' if metrics['total_trades'] >= 100 else '❌'} |
| 승률 | 45% | {metrics['win_rate']:.1%} | {'✅' if metrics['win_rate'] >= 0.45 else '❌'} |
| Profit Factor | 1.3 | {metrics['profit_factor']:.2f} | {'✅' if metrics['profit_factor'] >= 1.3 else '❌'} |
| 최대 낙폭 | 15% | {metrics['max_drawdown_pct']:.1f}% | {'✅' if metrics['max_drawdown_pct'] >= -15 else '❌'} |
| Sharpe | 0.8 | {metrics['sharpe']:.2f} | {'✅' if metrics['sharpe'] >= 0.8 else '❌'} |

## 청산 사유 분포

| 사유 | 횟수 |
|---|---|
{exit_table}
"""
    (out / f"{prefix}_report.md").write_text(report, encoding="utf-8")
    print(f"Reports saved to {out}/")
