"""
KW (강환국 류) 펀더멘털 전략 심층 검증 — Walk-Forward + Monte Carlo.

사용:
  python run_validation_kw.py --strategy super_value --method walkforward
  python run_validation_kw.py --strategy super_value --method monte_carlo --mc-runs 1000
  python run_validation_kw.py --strategy super_value --method all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading


def _generate_signals(strategy: str, panel, price_data, params, start, end):
    if strategy == "super_value":
        from src.strategy.kw_super_value import generate_super_value_signals
        return generate_super_value_signals(panel, price_data, params, start, end)
    elif strategy == "super_quality":
        from src.strategy.kw_super_quality import generate_super_quality_signals
        return generate_super_quality_signals(panel, price_data, params, start, end)
    else:
        from src.strategy.kw_ultra import generate_ultra_signals
        return generate_ultra_signals(panel, price_data, params, start, end)


# ═══════════════════════════════════════════════════════════════════════════
# Walk-Forward
def run_walkforward(strategy: str, train_years: int = 5, test_years: int = 1):
    print("\n" + "="*70)
    print(f"검증: Walk-Forward KW {strategy} (train {train_years}y / test {test_years}y)")
    print("="*70)

    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code": profile.code, "regime_index": profile.index_ticker, "currency": profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]
    params = dict(cfg[f"kw_{strategy}"])
    start_dt = "2014-01-01"
    end_dt   = pd.Timestamp.today().strftime("%Y-%m-%d")

    # 데이터
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start_dt, end_dt, min_bars=120, market="kr")
    if profile.index_ticker not in price_data:
        print(f"[STOP] {profile.index_ticker} 없음"); return

    # 펀더멘털 — 전체 기간 분기 일자
    rebal_months = params.get("rebalance_months", [5,8,11,4])
    rebal_dom    = params.get("rebalance_dom",    [16,16,16,1])
    raw_rebal = rebalance_dates_kr_quarterly(start_dt, end_dt, rebal_months, rebal_dom)
    calendar = price_data[profile.index_ticker].index
    rebal_dates = adjust_signals_to_trading(raw_rebal, calendar)
    print(f"  Loading fundamentals for {len(rebal_dates)} dates...")
    panel = build_fundamentals_panel(rebal_dates)
    if not panel:
        print("[STOP] panel 비어있음"); return

    # 윈도우
    all_dates = sorted(set(d for df in price_data.values() for d in df.index))
    s = pd.Timestamp(all_dates[0]); e = pd.Timestamp(all_dates[-1])
    windows = []
    t = s
    while True:
        tr_end = t + pd.DateOffset(years=train_years)
        te_end = tr_end + pd.DateOffset(years=test_years)
        if te_end > e: break
        windows.append((t, tr_end, te_end))
        t = t + pd.DateOffset(years=test_years)

    print(f"  Windows: {len(windows)}")

    wf_results = []
    for i, (tr_s, tr_e, te_e) in enumerate(windows):
        pd_tr = {s: df[(df.index >= tr_s) & (df.index < tr_e)] for s, df in price_data.items()}
        pd_te = {s: df[(df.index >= tr_e) & (df.index < te_e)] for s, df in price_data.items()}
        pd_tr = {s: df for s, df in pd_tr.items() if len(df) >= 60}
        pd_te = {s: df for s, df in pd_te.items() if len(df) >= 30}

        if len(pd_tr) < 30 or len(pd_te) < 30:
            print(f"  [skip {i}] train={len(pd_tr)} test={len(pd_te)}")
            continue
        if profile.index_ticker not in pd_tr or profile.index_ticker not in pd_te:
            continue

        # IS
        sigs_is = _generate_signals(strategy, panel, pd_tr, params, str(tr_s.date()), str(tr_e.date()))
        sigs_te = _generate_signals(strategy, panel, pd_te, params, str(tr_e.date()), str(te_e.date()))
        if not sigs_is or not sigs_te:
            continue
        try:
            eq_is, _   = run_quarterly_backtest(sigs_is, pd_tr, cfg, market="kr")
            m_is       = compute_quarterly_metrics(eq_is, cap)
            eq_oos, _  = run_quarterly_backtest(sigs_te, pd_te, cfg, market="kr")
            m_oos      = compute_quarterly_metrics(eq_oos, cap)
        except Exception as err:
            print(f"  [err {i}] {err}"); continue

        row = {
            "window":    f"{tr_s.year}-{tr_e.year}",
            "test":      f"{tr_e.year}-{te_e.year}",
            "IS_cagr":   round(m_is.get("cagr_pct", 0), 2),
            "OOS_cagr":  round(m_oos.get("cagr_pct", 0), 2),
            "IS_sharpe": round(m_is.get("sharpe", 0), 3),
            "OOS_sharpe":round(m_oos.get("sharpe", 0), 3),
            "IS_mdd":    round(m_is.get("max_drawdown_pct", 0), 2),
            "OOS_mdd":   round(m_oos.get("max_drawdown_pct", 0), 2),
        }
        wf_results.append(row)
        print(f"  [{row['window']} -> {row['test']}] IS CAGR {row['IS_cagr']:+.1f}%  "
              f"OOS CAGR {row['OOS_cagr']:+.1f}%  IS Sharpe {row['IS_sharpe']:.2f}  "
              f"OOS Sharpe {row['OOS_sharpe']:.2f}")

    if wf_results:
        df = pd.DataFrame(wf_results)
        oos_pos = (df["OOS_cagr"] > 0).sum()
        avg_oos = df["OOS_cagr"].mean()
        corr    = df["IS_cagr"].corr(df["OOS_cagr"])
        degrade = (df["OOS_cagr"] - df["IS_cagr"]).mean()
        print()
        print(f"  OOS 양수 비율: {oos_pos}/{len(df)} ({oos_pos/len(df)*100:.0f}%)")
        print(f"  평균 OOS CAGR: {avg_oos:+.2f}%")
        print(f"  IS-OOS 상관: {corr:.3f}")
        print(f"  평균 열화 (OOS-IS): {degrade:+.2f}%")

        out = Path(f"backtest_results/kr/validation_walkforward_kw_{strategy}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "windows": wf_results,
            "summary": {
                "oos_positive": int(oos_pos), "windows": int(len(df)),
                "avg_oos_cagr": float(avg_oos),
                "is_oos_corr": float(corr) if not pd.isna(corr) else None,
                "avg_degrade": float(degrade),
            },
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  결과: {out}")
    return wf_results


# ═══════════════════════════════════════════════════════════════════════════
# Monte Carlo — trade pnl shuffle
def run_monte_carlo(strategy: str, n_runs: int = 1000):
    print("\n" + "="*70)
    print(f"검증: Monte Carlo KW {strategy} ({n_runs:,} runs, trade pnl 셔플)")
    print("="*70)

    trades_path = Path(f"backtest_results/kr/kw_{strategy}_trades.csv")
    if not trades_path.exists():
        print(f"[STOP] {trades_path} 없음. 먼저 run_kw_backtest.py 실행 필요")
        return None
    df = pd.read_csv(trades_path)
    # buy + sell pairing 후 P&L 계산
    # simple: realized pnl = (sell value - buy value) per symbol per round
    # 간단 근사: 각 trade 의 net value 변동 누적
    df["signed_value"] = df.apply(
        lambda r: -r["value"] - r["fee"] if r["side"] == "buy" else r["value"] - r["fee"],
        axis=1,
    )
    # daily aggregation per symbol pair (complex)
    # 더 단순: equity curve 의 daily returns 사용
    eq_path = Path(f"backtest_results/kr/kw_{strategy}_equity.csv")
    eq = pd.read_csv(eq_path, index_col="date", parse_dates=["date"])["equity"]
    daily_ret = eq.pct_change().dropna().values

    if len(daily_ret) == 0:
        print("[STOP] returns 비어있음"); return None

    cap = 50_000_000.0
    actual_eq = cap * (1 + daily_ret).cumprod()
    actual_sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    roll = np.maximum.accumulate(actual_eq)
    actual_mdd = float(((actual_eq - roll) / roll).min() * 100)

    rng = np.random.default_rng(42)
    sharpes = np.zeros(n_runs); mdds = np.zeros(n_runs)
    print(f"  Shuffling {n_runs:,}...")
    for i in range(n_runs):
        s = daily_ret.copy(); rng.shuffle(s)
        eq_s = cap * (1 + s).cumprod()
        sharpes[i] = s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
        roll_s = np.maximum.accumulate(eq_s)
        mdds[i] = float(((eq_s - roll_s) / roll_s).min() * 100)

    p_sharpe = float((sharpes >= actual_sharpe).mean())
    p_mdd    = float((mdds <= actual_mdd).mean())

    result = {
        "n_days": len(daily_ret), "n_trades": len(df),
        "actual_sharpe": round(actual_sharpe, 4),
        "actual_mdd_pct": round(actual_mdd, 2),
        "mc_runs": n_runs,
        "mc_sharpe_mean": round(float(sharpes.mean()), 4),
        "mc_sharpe_p5":   round(float(np.percentile(sharpes, 5)), 4),
        "mc_sharpe_p95":  round(float(np.percentile(sharpes, 95)), 4),
        "mc_mdd_p5":      round(float(np.percentile(mdds, 5)), 2),
        "mc_mdd_p95":     round(float(np.percentile(mdds, 95)), 2),
        "p_value_sharpe": round(p_sharpe, 4),
        "p_value_mdd":    round(p_mdd, 4),
    }
    print(f"\n  실측 Sharpe: {actual_sharpe:.3f}")
    print(f"  MC Sharpe 5%~95%: {np.percentile(sharpes,5):.3f} ~ {np.percentile(sharpes,95):.3f}")
    print(f"  실측 MDD: {actual_mdd:+.2f}%")
    print(f"  MC MDD  5%~95%: {np.percentile(mdds,5):+.2f}% ~ {np.percentile(mdds,95):+.2f}%")
    print(f"  p-value(Sharpe): {p_sharpe:.4f}")
    print(f"  p-value(MDD):    {p_mdd:.4f}")

    out = Path(f"backtest_results/kr/validation_monte_carlo_kw_{strategy}.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  결과: {out}")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["super_value", "super_quality", "ultra"], default="super_value")
    p.add_argument("--method", choices=["walkforward", "monte_carlo", "all"], default="all")
    p.add_argument("--train-years", type=int, default=5)
    p.add_argument("--test-years",  type=int, default=1)
    p.add_argument("--mc-runs",     type=int, default=1000)
    args = p.parse_args()

    if args.method in ("walkforward", "all"):
        run_walkforward(args.strategy, args.train_years, args.test_years)
    if args.method in ("monte_carlo", "all"):
        run_monte_carlo(args.strategy, args.mc_runs)
