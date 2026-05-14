"""
전략 심층 검증 스크립트

방법:
  1. 기간 확장   : 2000-2026 (닷컴버블·금융위기 포함)
  2. Monte Carlo : 거래 순서 셔플 → 통계적 유의성 검증
  3. Walk-Forward: train/test 롤링 → OOS vs IS 비교
  4. Russell 2000: 소형주 유니버스 → 대형주 편향 확인

사용:
  python run_validation.py --method period   [--refresh]
  python run_validation.py --method monte_carlo
  python run_validation.py --method walkforward
  python run_validation.py --method russell
  python run_validation.py --method all      [--refresh]
"""
import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


# ── 공통 유틸 ──────────────────────────────────────────────────────────────
def load_cfg(path="config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _sharpe(returns: pd.Series, rf: float = 0.0) -> float:
    ex = returns - rf / 252
    return float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else 0.0


def _mdd(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(dd.min() * 100)


def _cagr(equity: pd.Series) -> float:
    n_years = len(equity) / 252
    if n_years <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1) * 100


# ═══════════════════════════════════════════════════════════════════════════
# 1. 기간 확장 (2000-2026)

def run_period_extension(refresh: bool = False):
    print("\n" + "="*60)
    print("검증 1: 기간 확장 (2000-01-01 ~ 현재)")
    print("="*60)

    from src.fetch.universe import get_sp500_tickers
    from src.fetch.prices import fetch_all
    from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics
    from src.backtest.weinstein_engine import run_weinstein_backtest
    from src.backtest.metrics import compute_metrics

    cfg_ext = load_cfg("config_extended.yaml")
    cfg_orig = load_cfg("config.yaml")

    tickers = get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + tickers

    start = cfg_ext["data"]["start_date"]
    end   = cfg_ext["data"]["end_date"]
    cache = cfg_ext["data"]["cache_dir"]
    cap   = cfg_ext["backtest"]["initial_capital_usd"]

    print(f"데이터 로딩 ({len(tickers)} tickers, {start} ~ today)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end,
                           min_bars=200, refresh=refresh, cache_dir=cache)
    print(f"  {len(price_data)} 종목 로드 ({time.time()-t0:.0f}s)")

    results = {}

    # ── Clenow ──────────────────────────────────────────────────────────
    print("\n[Clenow] 실행 중...")
    equity_cl, _ = run_clenow_backtest(price_data, cfg_ext)
    m_cl = compute_clenow_metrics(equity_cl, cap)
    results["clenow"] = m_cl
    _print_compare("Clenow", m_cl, _orig_metrics("clenow"))
    equity_cl.to_frame("equity").to_csv(
        Path("backtest_results") / "clenow_ext_equity.csv", index_label="date")

    # ── Weinstein ────────────────────────────────────────────────────────
    print("\n[Weinstein] 실행 중...")
    from src.strategy.weinstein_stage2 import generate_weinstein_signals
    all_sigs = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        sigs = generate_weinstein_signals(sym, df, cfg_ext["weinstein_strategy"])
        all_sigs.extend(sigs)

    from src.backtest.weinstein_engine import run_weinstein_backtest
    spy_w = price_data["SPY"]["close"] if "SPY" in price_data else None
    result_w = run_weinstein_backtest(all_sigs, price_data, spy_w, cfg_ext)
    m_w = compute_metrics(result_w)
    results["weinstein"] = m_w
    _print_compare("Weinstein", m_w, _orig_metrics("weinstein"))
    result_w.equity_curve.to_frame("equity").to_csv(
        Path("backtest_results") / "weinstein_ext_equity.csv", index_label="date")

    # ── Phase 4 ──────────────────────────────────────────────────────────
    print("\n[Phase 4] 실행 중...")
    from src.indicators.regime import compute_regime
    from src.indicators.factors import build_factor_matrices
    from src.strategy.factor_stack import generate_factor_signals
    from src.backtest.engine import run_backtest

    p1 = cfg_ext["phase1_breakout_pullback"]
    p2 = cfg_ext["phase2_cloud_support"]
    p3 = cfg_ext["phase3_hybrid"]
    p4 = cfg_ext["phase4_factor_stack"]
    p2f = dict(p2)
    p2f["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2f["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
    p4["momentum_period"] = p4.get("momentum_period", 63)

    regime_p4 = compute_regime(
        start, end,
        ma_short=cfg_ext["regime_filter"]["spy_ma_short"],
        ma_long=cfg_ext["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg_ext["regime_filter"]["vix_threshold"],
    )

    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data, mom_period=p4["momentum_period"])

    all_sigs_p4 = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        sigs = generate_factor_signals(sym, df, p1, p2f, p4, mom_rank, bbw_rank, spy_mom)
        all_sigs_p4.extend(sigs)

    result_p4 = run_backtest(all_sigs_p4, price_data, regime_p4, cfg_ext)
    m_p4 = compute_metrics(result_p4)
    results["phase4"] = m_p4
    _print_compare("Phase 4", m_p4, _orig_metrics("phase4"))

    _save_results("period_extension", results)
    print("\n결과 저장: backtest_results/validation_period_extension.json")


def _orig_metrics(strategy: str) -> dict:
    """기존 2015-2026 결과 (하드코딩 — BACKTEST_RESULTS.md 기준)."""
    orig = {
        "clenow":   {"cagr_pct": 16.92, "max_drawdown_pct": -19.6, "sharpe": 1.10},
        "weinstein":{"cagr_pct":  8.0,  "max_drawdown_pct": -14.0, "sharpe": 0.88},
        "phase4":   {"cagr_pct":  8.0,  "max_drawdown_pct": -11.92,"sharpe": 0.74},
    }
    return orig.get(strategy, {})


def _print_compare(name: str, new: dict, orig: dict):
    print(f"\n  {'지표':20s} {'2000-2026':>12} {'2015-2026':>12} {'변화':>10}")
    print("  " + "-"*56)
    for key, label in [("cagr_pct","CAGR%"), ("max_drawdown_pct","MDD%"), ("sharpe","Sharpe")]:
        nv = new.get(key, 0)
        ov = orig.get(key, 0)
        diff = nv - ov
        print(f"  {label:20s} {nv:>12.2f} {ov:>12.2f} {diff:>+10.2f}")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Monte Carlo — Trade Shuffle
# ═══════════════════════════════════════════════════════════════════════════
def run_monte_carlo(n_runs: int = 10000):
    print("\n" + "="*60)
    print(f"검증 2: Monte Carlo Trade Shuffle ({n_runs:,} runs)")
    print("="*60)

    results = {}
    trade_files = {
        "phase4":   Path("backtest_results/phase4_trades.csv"),
        "clenow":   Path("paper_trading/trades_clenow.csv"),   # 페이퍼 트레이딩 실거래 기록
        "weinstein":Path("backtest_results/weinstein_trades.csv"),
    }

    for strategy, path in trade_files.items():
        if not path.exists():
            alt = list(Path("backtest_results").glob(f"*{strategy}*trades*.csv"))
            if not alt:
                print(f"  [{strategy}] trades file not found -- skip")
                continue
            path = alt[0]

        try:
            df = pd.read_csv(path)
        except Exception:
            print(f"  [{strategy}] CSV read failed -- skip")
            continue
        if df.empty or "pnl" not in df.columns:
            print(f"  [{strategy}] pnl column missing -- skip")
            continue

        pnls = df["pnl"].dropna().values
        cap  = 100_000.0

        # 실제 equity curve 기반 지표
        actual_sharpe, actual_mdd = _equity_metrics(pnls, cap)

        # 셔플 시뮬레이션 — 거래 순서를 바꿔 equity curve path 변화 측정
        shuffled_sharpes = []
        shuffled_mdds    = []
        for _ in range(n_runs):
            s = pnls.copy()
            np.random.shuffle(s)
            sh, md = _equity_metrics(s, cap)
            shuffled_sharpes.append(sh)
            shuffled_mdds.append(md)

        # p-value: 실제 Sharpe가 셔플 분포에서 상위 몇 %인가
        p_sharpe = (np.array(shuffled_sharpes) >= actual_sharpe).mean()
        p_mdd    = (np.array(shuffled_mdds) <= actual_mdd).mean()  # MDD는 낮을수록 좋음

        results[strategy] = {
            "n_trades":          len(pnls),
            "actual_sharpe":     round(actual_sharpe, 4),
            "actual_mdd_pct":    round(actual_mdd, 2),
            "mc_sharpe_mean":    round(np.mean(shuffled_sharpes), 4),
            "mc_sharpe_p95":     round(np.percentile(shuffled_sharpes, 95), 4),
            "p_value_sharpe":    round(p_sharpe, 4),
            "p_value_mdd":       round(p_mdd, 4),
            "significant_5pct":  p_sharpe < 0.05,
        }

        sig = "SIGNIFICANT (p<0.05)" if p_sharpe < 0.05 else "not significant"
        print(f"\n  [{strategy}]")
        print(f"    trades:          {len(pnls)}")
        print(f"    actual Sharpe:   {actual_sharpe:.4f}")
        print(f"    actual MDD:      {actual_mdd:.2f}%")
        print(f"    MC Sharpe mean:  {np.mean(shuffled_sharpes):.4f}")
        print(f"    MC Sharpe p95:   {np.percentile(shuffled_sharpes,95):.4f}")
        print(f"    p-value(Sharpe): {p_sharpe:.4f}  -> {sig}")

    _save_results("monte_carlo", results)
    print("\n결과 저장: backtest_results/validation_monte_carlo.json")


def _equity_metrics(pnls: np.ndarray, initial_capital: float = 100_000.0):
    """거래 PnL 시퀀스 -> equity curve -> (Sharpe, MDD%)."""
    equity = np.zeros(len(pnls) + 1)
    equity[0] = initial_capital
    for i, p in enumerate(pnls):
        equity[i + 1] = equity[i] + p
    eq = pd.Series(equity)
    daily_ret = eq.pct_change().dropna()
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    roll_max = eq.cummax()
    mdd = float(((eq - roll_max) / roll_max).min() * 100)
    return sharpe, mdd


# ═══════════════════════════════════════════════════════════════════════════
# 3. Walk-Forward Analysis
# ═══════════════════════════════════════════════════════════════════════════
def run_walkforward(train_years: int = 4, test_years: int = 1):
    print("\n" + "="*60)
    print(f"검증 3: Walk-Forward (train {train_years}y / test {test_years}y)")
    print("="*60)

    from src.fetch.universe import get_sp500_tickers
    from src.fetch.prices import fetch_all
    from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics

    cfg = load_cfg()
    tickers = get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + tickers

    # 전체 기간 데이터 (2015~)
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=200)

    # 날짜 범위 파악
    all_dates = sorted(set(
        d for df in price_data.values() for d in df.index
    ))
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
        t = t + pd.DateOffset(years=test_years)  # 1년씩 슬라이딩

    print(f"  윈도우 {len(windows)}개: {windows[0][0].year}~{windows[-1][2].year}")

    # Clenow 기준으로 Walk-forward (대표 전략)
    wf_results = []
    for i, (tr_start, tr_end, te_end) in enumerate(windows):
        # 이 윈도우에 해당하는 데이터 슬라이싱
        pd_train = {s: df[(df.index >= tr_start) & (df.index < tr_end)]
                    for s, df in price_data.items()}
        pd_test  = {s: df[(df.index >= tr_end) & (df.index < te_end)]
                    for s, df in price_data.items()}

        pd_train = {s: df for s, df in pd_train.items() if len(df) >= 150}
        pd_test  = {s: df for s, df in pd_test.items()  if len(df) >= 50}

        if len(pd_train) < 10 or len(pd_test) < 10:
            continue

        cfg_w = {**cfg, "data": {**cfg["data"],
                                  "start_date": str(tr_start.date()),
                                  "end_date":   str(tr_end.date())}}
        cfg_t = {**cfg, "data": {**cfg["data"],
                                  "start_date": str(tr_end.date()),
                                  "end_date":   str(te_end.date())}}

        # IS (train) 성과
        try:
            eq_is, _ = run_clenow_backtest(pd_train, cfg_w)
            m_is = compute_clenow_metrics(eq_is, cfg["backtest"]["initial_capital_usd"])
        except Exception:
            continue

        # OOS (test) 성과 — 파라미터 고정, 다른 기간 적용
        try:
            eq_oos, _ = run_clenow_backtest(pd_test, cfg_t)
            m_oos = compute_clenow_metrics(eq_oos, cfg["backtest"]["initial_capital_usd"])
        except Exception:
            continue

        row = {
            "window": f"{tr_start.year}-{tr_end.year}",
            "test":   f"{tr_end.year}-{te_end.year}",
            "IS_cagr":   round(m_is.get("cagr_pct", 0), 2),
            "OOS_cagr":  round(m_oos.get("cagr_pct", 0), 2),
            "IS_sharpe": round(m_is.get("sharpe", 0), 3),
            "OOS_sharpe":round(m_oos.get("sharpe", 0), 3),
            "IS_mdd":    round(m_is.get("max_drawdown_pct", 0), 2),
            "OOS_mdd":   round(m_oos.get("max_drawdown_pct", 0), 2),
        }
        wf_results.append(row)
        print(f"  [{row['window']}→{row['test']}] "
              f"IS CAGR {row['IS_cagr']:+.1f}%  OOS CAGR {row['OOS_cagr']:+.1f}%  "
              f"IS Sharpe {row['IS_sharpe']:.2f}  OOS Sharpe {row['OOS_sharpe']:.2f}")

    if wf_results:
        df = pd.DataFrame(wf_results)
        corr = df["IS_cagr"].corr(df["OOS_cagr"])
        degrade = (df["OOS_cagr"] - df["IS_cagr"]).mean()
        print(f"\n  IS↔OOS CAGR 상관계수: {corr:.3f}  (1=완벽, 0=무관계)")
        print(f"  평균 성과 열화:       {degrade:+.2f}%  (IS 대비 OOS 차이)")
        if corr > 0.5:
            print("  -> 전략이 기간 변화에 안정적 (과적합 위험 낮음)")
        else:
            print("  -> IS↔OOS 상관 낮음 (과적합 가능성 있음)")

        _save_results("walkforward_clenow", {"windows": wf_results,
                                              "is_oos_corr": round(corr, 4),
                                              "mean_degradation": round(degrade, 2)})
    print("\n결과 저장: backtest_results/validation_walkforward_clenow.json")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Russell 2000 유니버스
# ═══════════════════════════════════════════════════════════════════════════
def run_russell2000(refresh: bool = False):
    print("\n" + "="*60)
    print("검증 4: Russell 2000 유니버스 (소형주)")
    print("="*60)

    from src.fetch.universe import get_russell2000_tickers
    from src.fetch.prices import fetch_all
    from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics

    cfg = load_cfg()
    tickers = get_russell2000_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + tickers

    print(f"Russell 2000 {len(tickers)} 종목 로딩...")
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=200,
                           refresh=refresh, cache_dir="data/raw_russell")
    print(f"  {len(price_data)} 종목 로드")

    cap = cfg["backtest"]["initial_capital_usd"]

    print("\n[Clenow on Russell 2000] 실행 중...")
    equity_cl, _ = run_clenow_backtest(price_data, cfg)
    m_cl = compute_clenow_metrics(equity_cl, cap)
    _print_compare("Clenow (Russell 2000)", m_cl, _orig_metrics("clenow"))

    print("\n[Weinstein on Russell 2000] 실행 중...")
    from src.strategy.weinstein_stage2 import generate_weinstein_signals
    from src.backtest.weinstein_engine import run_weinstein_backtest
    from src.backtest.metrics import compute_metrics
    all_sigs = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        sigs = generate_weinstein_signals(sym, df, cfg["weinstein_strategy"])
        all_sigs.extend(sigs)
    spy_rw = price_data["SPY"]["close"] if "SPY" in price_data else None
    result_w = run_weinstein_backtest(all_sigs, price_data, spy_rw, cfg)
    m_w = compute_metrics(result_w)
    _print_compare("Weinstein (Russell 2000)", m_w, _orig_metrics("weinstein"))

    _save_results("russell2000", {"clenow": m_cl, "weinstein": m_w})
    print("\n결과 저장: backtest_results/validation_russell2000.json")


# ── 저장 ──────────────────────────────────────────────────────────────────
def _save_results(name: str, data: dict):
    out = Path("backtest_results") / f"validation_{name}.json"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# ── CLI ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--method", choices=["period","monte_carlo","walkforward","russell","all"],
                   required=True)
    p.add_argument("--refresh", action="store_true", help="캐시 무시하고 재다운로드")
    p.add_argument("--mc-runs", type=int, default=10000, help="Monte Carlo 반복 수")
    args = p.parse_args()

    if args.method in ("period", "all"):
        run_period_extension(refresh=args.refresh)
    if args.method in ("monte_carlo", "all"):
        run_monte_carlo(n_runs=args.mc_runs)
    if args.method in ("walkforward", "all"):
        run_walkforward()
    if args.method in ("russell", "all"):
        run_russell2000(refresh=args.refresh)
