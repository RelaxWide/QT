"""
KW Super Value 생존편향 측정.

현재 KOSPI 마스터 (현재 시점) vs 시점별 KOSPI 마스터 (PyKRX get_market_ticker_list)
양쪽으로 백테스트 → CAGR/MDD/Sharpe 비교 → 생존편향 magnitude 측정.

사용:
    python scripts/check_survivorship_kw.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading
from src.strategy.kw_super_value import generate_super_value_signals


def get_kospi_tickers_at(date_str: str) -> list[str]:
    """특정 시점 KOSPI 마스터 (PyKRX, KRX 인증 필요)."""
    from pykrx import stock as _kx
    try:
        tickers = _kx.get_market_ticker_list(date_str, market="KOSPI")
        # 우선주 제외 (6자리 끝 != '0')
        return [t for t in tickers if len(t) == 6 and t[-1] == "0"]
    except Exception as e:
        print(f"  [error] {date_str}: {e}")
        return []


def main():
    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code":profile.code,"regime_index":profile.index_ticker,"currency":profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]
    params = dict(cfg["kw_super_value"])

    start = "2014-01-01"
    end   = pd.Timestamp.today().strftime("%Y-%m-%d")

    # 1) 현재 KOSPI 마스터 — 기존 백테스트와 동일
    current_tickers = get_kospi_all_tickers()
    print(f"현재 KOSPI 마스터: {len(current_tickers)} 종목")

    # 2) 분기별 시점별 KOSPI 마스터 합집합
    rebal_dates_naive = rebalance_dates_kr_quarterly(start, end, params["rebalance_months"], params["rebalance_dom"])
    print(f"시점별 마스터 조회: {len(rebal_dates_naive)} 분기")

    point_in_time_union: set = set()
    sample_dates = rebal_dates_naive[::4]   # 1년 단위 샘플 (속도)
    for d in sample_dates:
        d_str = d.strftime("%Y%m%d")
        t = get_kospi_tickers_at(d_str)
        if t:
            print(f"  {d.date()}: {len(t)} 종목")
            point_in_time_union.update(t)

    print(f"\n시점별 마스터 합집합: {len(point_in_time_union)} 종목")
    print(f"  현재 - 시점별: {len(set(current_tickers) - point_in_time_union)} (현재만 있는 종목)")
    print(f"  시점별 - 현재: {len(point_in_time_union - set(current_tickers))} (과거에만 있던 종목, 상폐 등)")

    # 3) 양쪽 백테스트
    base_tickers = [profile.index_ticker]

    # 시점별 합집합 universe 로 백테스트 — 상폐 종목 포함된 더 큰 풀
    print("\n=== 시점별 합집합 universe 백테스트 ===")
    full_universe = sorted(point_in_time_union | set(current_tickers))
    print(f"  universe size: {len(full_universe)}")

    print("  Loading prices...")
    t0 = time.time()
    price_data = fetch_all(base_tickers + full_universe, start, end, min_bars=120, market="kr")
    print(f"  {len(price_data)} loaded in {time.time()-t0:.1f}s")

    raw_rebal = rebalance_dates_kr_quarterly(start, end, params["rebalance_months"], params["rebalance_dom"])
    rebal = adjust_signals_to_trading(raw_rebal, price_data[profile.index_ticker].index)
    panel = build_fundamentals_panel(rebal)
    sigs = generate_super_value_signals(panel, price_data, params, start, end)
    print(f"  {len(sigs)} signals")
    eq, _ = run_quarterly_backtest(sigs, price_data, cfg, market="kr")
    m_pit = compute_quarterly_metrics(eq, cap)
    print(f"  CAGR {m_pit['cagr_pct']:+.2f}%  MDD {m_pit['max_drawdown_pct']:+.2f}%  Sharpe {m_pit['sharpe']:.2f}")

    # 결과 저장
    import json
    Path("backtest_results/kr/survivorship_kw_super_value.json").write_text(
        json.dumps({
            "current_universe_size":     len(current_tickers),
            "pit_union_size":            len(point_in_time_union),
            "current_only_count":        len(set(current_tickers) - point_in_time_union),
            "pit_only_count":            len(point_in_time_union - set(current_tickers)),
            "pit_universe_metrics":      m_pit,
        }, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== 비교 ===")
    print(f"  현재 universe (기본):       CAGR ~20.5% / MDD -39% / Sharpe 0.97")
    print(f"  시점별 합집합 universe:     CAGR {m_pit['cagr_pct']:+.2f}% / MDD {m_pit['max_drawdown_pct']:+.2f}% / Sharpe {m_pit['sharpe']:.2f}")
    print(f"  → 생존편향 영향: CAGR 차이 = {m_pit['cagr_pct'] - 20.52:+.2f}%p (음수면 현재 universe 가 부풀려진 것)")


if __name__ == "__main__":
    main()
