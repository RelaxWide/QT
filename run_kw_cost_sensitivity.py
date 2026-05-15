"""
KW Super Value 거래세·슬리피지 sensitivity.

slippage_pct {0.1, 0.3, 0.5, 1.0, 2.0} × KR_fee_multiplier {1.0, 1.5, 2.0} 그리드.
실전 비용 모델 강도에 따른 영향 측정.

사용:
    python run_kw_cost_sensitivity.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading
from src.strategy.kw_super_value import generate_super_value_signals


SLIPPAGE_GRID = [0.1, 0.3, 0.5, 1.0, 2.0]   # 백분율
FEE_MULT_GRID = [1.0, 1.5, 2.0]              # 기본 KR fee 의 배수 (1.0 = 0.18% sell)


def main():
    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code":profile.code,"regime_index":profile.index_ticker,"currency":profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]
    params = dict(cfg["kw_super_value"])
    base_fee_sell = 0.00015 + 0.0018 + 0.00023   # 0.218%

    start = "2014-01-01"
    end   = pd.Timestamp.today().strftime("%Y-%m-%d")

    print("Loading data...")
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start, end, min_bars=120, market="kr")
    raw_rebal = rebalance_dates_kr_quarterly(start, end, params["rebalance_months"], params["rebalance_dom"])
    rebal_dates = adjust_signals_to_trading(raw_rebal, price_data[profile.index_ticker].index)
    panel = build_fundamentals_panel(rebal_dates)
    sigs  = generate_super_value_signals(panel, price_data, params, start, end)
    print(f"  Signals: {len(sigs)}")

    rows = []
    for sl in SLIPPAGE_GRID:
        for fm in FEE_MULT_GRID:
            cfg_t = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
            cfg_t["risk"] = dict(cfg_t.get("risk", {}))
            cfg_t["risk"]["slippage_pct"] = sl
            # commission 0.015% buy + 0.218% sell × multiplier — make_cost_model 에 직접 주입
            # 기존 cost_model 은 cfg["risk"]["commission_pct"] 사용 가능. 양방향 비례.
            cfg_t["risk"]["commission_pct"] = 0.015 * fm   # buy 측 (기본 0.015%)
            cfg_t["risk"]["sell_commission_pct"] = (base_fee_sell * 100) * fm  # 매도 강화
            t0 = time.time()
            eq, _ = run_quarterly_backtest(sigs, price_data, cfg_t, market="kr")
            m = compute_quarterly_metrics(eq, cap)
            row = {
                "slippage_pct": sl,
                "fee_mult": fm,
                "cagr": m["cagr_pct"],
                "mdd":  m["max_drawdown_pct"],
                "sharpe": m["sharpe"],
                "calmar": m["calmar"],
                "elapsed": round(time.time()-t0, 1),
            }
            rows.append(row)
            print(f"  sl={sl:.1f}% fee×{fm}: CAGR {m['cagr_pct']:+6.2f}%  MDD {m['max_drawdown_pct']:+6.2f}%  "
                  f"Sharpe {m['sharpe']:.2f}")

    df = pd.DataFrame(rows)
    out = Path("backtest_results/kr/kw_super_value_cost_sensitivity.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nSaved: {out}")

    print("\n=== 보수 시나리오 (slippage 1.0% + fee×1.5) ===")
    row = df[(df["slippage_pct"] == 1.0) & (df["fee_mult"] == 1.5)].iloc[0]
    print(f"  CAGR {row['cagr']:+6.2f}% / MDD {row['mdd']:+6.2f}% / Sharpe {row['sharpe']:.2f}")

    print("=== 극단 시나리오 (slippage 2.0% + fee×2.0) ===")
    row = df[(df["slippage_pct"] == 2.0) & (df["fee_mult"] == 2.0)].iloc[0]
    print(f"  CAGR {row['cagr']:+6.2f}% / MDD {row['mdd']:+6.2f}% / Sharpe {row['sharpe']:.2f}")


if __name__ == "__main__":
    main()
