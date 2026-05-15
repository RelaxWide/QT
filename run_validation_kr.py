"""
KR Clenow 심층 검증 스크립트 (Walk-Forward + Monte Carlo).

US 의 run_validation.py 와 동일 로직을 KR universe (KOSPI200) + KRW 자본 으로 실행.

사용:
  python run_validation_kr.py --method walkforward     # 3년 학습 / 1년 OOS 슬라이딩
  python run_validation_kr.py --method monte_carlo     # 트레이드 셔플 1000회
  python run_validation_kr.py --method all
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.fetch.universe import get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics
from src.markets import get_profile


def load_cfg(path="config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _equity_metrics(pnls: np.ndarray, initial_cap: float) -> tuple[float, float]:
    """pnl 시리즈 → equity curve → Sharpe, MDD"""
    equity = initial_cap + np.cumsum(pnls)
    ret    = np.diff(equity) / equity[:-1]
    if len(ret) == 0 or ret.std() == 0:
        return 0.0, 0.0
    sharpe = ret.mean() / ret.std() * np.sqrt(252)
    roll_max = np.maximum.accumulate(equity)
    dd       = (equity - roll_max) / roll_max
    return float(sharpe), float(dd.min() * 100)


# ═══════════════════════════════════════════════════════════════════════════
# Walk-Forward

def run_walkforward(train_years: int = 3, test_years: int = 1):
    print("\n" + "="*60)
    print(f"검증: Walk-Forward KR (train {train_years}y / test {test_years}y)")
    print("="*60)

    profile = get_profile("kr")
    cfg     = load_cfg()
    cfg["market"] = {"code": profile.code, "regime_index": profile.index_ticker,
                     "currency": profile.currency}
    cl_p = cfg.setdefault("clenow_strategy", {})
    cl_p.setdefault("min_price",    profile.min_price)
    cl_p.setdefault("index_ticker", profile.index_ticker)
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]

    tickers = get_kospi200_tickers()
    if profile.index_ticker not in tickers:
        tickers = [profile.index_ticker] + tickers

    print(f"  Loading {len(tickers)} tickers ...")
    t0 = time.time()
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=200, market="kr")
    print(f"  {len(price_data)} loaded in {time.time()-t0:.1f}s")

    # 날짜 범위
    all_dates = sorted(set(d for df in price_data.values() for d in df.index))
    start = pd.Timestamp(all_dates[0])
    end   = pd.Timestamp(all_dates[-1])

    windows = []
    t = start
    while True:
        train_end = t + pd.DateOffset(years=train_years)
        test_end  = train_end + pd.DateOffset(years=test_years)
        if test_end > end:
            break
        windows.append((t, train_end, test_end))
        t = t + pd.DateOffset(years=test_years)

    print(f"  Windows: {len(windows)}")

    wf_results = []
    for i, (tr_s, tr_e, te_e) in enumerate(windows):
        pd_tr = {s: df[(df.index >= tr_s) & (df.index < tr_e)]
                 for s, df in price_data.items()}
        pd_te = {s: df[(df.index >= tr_e) & (df.index < te_e)]
                 for s, df in price_data.items()}
        pd_tr = {s: df for s, df in pd_tr.items() if len(df) >= 150}
        pd_te = {s: df for s, df in pd_te.items() if len(df) >= 50}

        if len(pd_tr) < 30 or len(pd_te) < 30:
            print(f"  [skip] window {i}: train={len(pd_tr)} test={len(pd_te)}")
            continue
        if profile.index_ticker not in pd_tr or profile.index_ticker not in pd_te:
            print(f"  [skip] window {i}: regime index 없음")
            continue

        cfg_tr = {**cfg, "data": {**cfg["data"], "start_date": str(tr_s.date()), "end_date": str(tr_e.date())}}
        cfg_te = {**cfg, "data": {**cfg["data"], "start_date": str(tr_e.date()), "end_date": str(te_e.date())}}

        try:
            eq_is, _  = run_clenow_backtest(pd_tr, cfg_tr)
            m_is      = compute_clenow_metrics(eq_is, cap)
            eq_oos, _ = run_clenow_backtest(pd_te, cfg_te)
            m_oos     = compute_clenow_metrics(eq_oos, cap)
        except Exception as e:
            print(f"  [err] window {i}: {e}")
            continue

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
        print(f"  평균 열화: {degrade:+.2f}%")

        out = Path("backtest_results/kr/validation_walkforward_clenow.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "windows": wf_results,
            "summary": {
                "oos_positive": int(oos_pos),
                "windows":      int(len(df)),
                "avg_oos_cagr": float(avg_oos),
                "is_oos_corr":  float(corr) if not pd.isna(corr) else None,
                "avg_degrade":  float(degrade),
            },
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  결과: {out}")

    return wf_results


# ═══════════════════════════════════════════════════════════════════════════
# Monte Carlo

def run_monte_carlo(n_runs: int = 1000):
    print("\n" + "="*60)
    print(f"검증: Monte Carlo KR Trade Shuffle ({n_runs:,} runs)")
    print("="*60)

    # KR 백테스트 결과의 trades 사용 (clenow_equity.csv 에서 daily return 사용)
    eq_path = Path("backtest_results/kr/clenow_equity.csv")
    if not eq_path.exists():
        print(f"  {eq_path} 없음. 먼저 python run_clenow.py --market kr 실행 필요")
        return None

    eq = pd.read_csv(eq_path, index_col="date", parse_dates=["date"])["equity"]
    daily_ret = eq.pct_change().dropna().values

    if len(daily_ret) == 0:
        print("  daily return 비어있음 - 종료")
        return None

    cap = 50_000_000.0
    print(f"  daily_returns: {len(daily_ret)} 일")

    # 실측 metrics
    actual_eq = cap * (1 + daily_ret).cumprod()
    actual_sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    roll = np.maximum.accumulate(actual_eq)
    actual_mdd = float(((actual_eq - roll) / roll).min() * 100)

    # 셔플
    print(f"  Shuffling {n_runs:,} times ...")
    rng = np.random.default_rng(42)
    sharpes = np.zeros(n_runs)
    mdds    = np.zeros(n_runs)
    for i in range(n_runs):
        s = daily_ret.copy()
        rng.shuffle(s)
        eq_s = cap * (1 + s).cumprod()
        sh = s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
        roll_s = np.maximum.accumulate(eq_s)
        md = float(((eq_s - roll_s) / roll_s).min() * 100)
        sharpes[i] = sh
        mdds[i]    = md

    p_sharpe = float((sharpes >= actual_sharpe).mean())
    p_mdd    = float((mdds <= actual_mdd).mean())

    result = {
        "n_days":         len(daily_ret),
        "actual_sharpe":  round(float(actual_sharpe), 4),
        "actual_mdd_pct": round(actual_mdd, 2),
        "mc_runs":        n_runs,
        "mc_sharpe_mean": round(float(sharpes.mean()), 4),
        "mc_sharpe_std":  round(float(sharpes.std()), 4),
        "mc_sharpe_p5":   round(float(np.percentile(sharpes, 5)), 4),
        "mc_sharpe_p95":  round(float(np.percentile(sharpes, 95)), 4),
        "mc_mdd_p5":      round(float(np.percentile(mdds, 5)), 2),
        "mc_mdd_p95":     round(float(np.percentile(mdds, 95)), 2),
        "p_value_sharpe": round(p_sharpe, 4),
        "p_value_mdd":    round(p_mdd, 4),
    }

    print(f"\n  실측 Sharpe:  {actual_sharpe:.3f}")
    print(f"  MC Sharpe 평균: {sharpes.mean():.3f}  (5%~95%: {np.percentile(sharpes,5):.3f} ~ {np.percentile(sharpes,95):.3f})")
    print(f"  실측 MDD:     {actual_mdd:+.2f}%")
    print(f"  MC MDD (5%~95%): {np.percentile(mdds,5):+.2f}% ~ {np.percentile(mdds,95):+.2f}%")
    print(f"  p-value(Sharpe): {p_sharpe:.4f}  (낮을수록 통계적 의미 강함)")
    print(f"  p-value(MDD):    {p_mdd:.4f}")

    out = Path("backtest_results/kr/validation_monte_carlo_clenow.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  결과: {out}")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--method", choices=["walkforward", "monte_carlo", "all"], default="all")
    p.add_argument("--train-years", type=int, default=3)
    p.add_argument("--test-years",  type=int, default=1)
    p.add_argument("--mc-runs",     type=int, default=1000)
    args = p.parse_args()

    if args.method in ("walkforward", "all"):
        run_walkforward(args.train_years, args.test_years)
    if args.method in ("monte_carlo", "all"):
        run_monte_carlo(args.mc_runs)
