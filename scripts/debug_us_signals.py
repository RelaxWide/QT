"""
US Clenow/Weinstein 신호 직접 산출 + 진단.

is_wed 와 무관하게 buy/sell 후보 강제 출력.
SPY MA200, VIX, 종목별 score 등도 확인.

사용:
    python scripts/debug_us_signals.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from paper_trading.live_signals import get_clenow_signals, get_weinstein_signals


def main():
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    print("Fetching prices (with cache)...")
    tickers = ["SPY", "^VIX"] + get_sp500_tickers()
    price_data = fetch_all(tickers, cfg["data"]["start_date"], None, min_bars=150)
    print(f"  Loaded {len(price_data)} tickers")

    # 데이터 최신일
    latest = max(df.index.max() for df in price_data.values() if not df.empty)
    today = latest
    print(f"  Latest data: {today.date()}")

    # SPY 레짐 확인
    spy = price_data.get("SPY")
    if spy is not None and not spy.empty:
        spy_last = spy.iloc[-1]["close"]
        spy_ma200 = spy["close"].rolling(200).mean().iloc[-1]
        regime_ok = spy_last > spy_ma200
        print(f"\n[Regime] SPY={spy_last:.2f}, MA200={spy_ma200:.2f}  →  {'BULL ✅' if regime_ok else 'BEAR ❌ (Clenow 차단)'}")

    # VIX 확인
    vix = price_data.get("^VIX")
    if vix is not None and not vix.empty:
        vix_last = vix.iloc[-1]["close"]
        vix_thresh = cfg.get("regime_filter", {}).get("vix_threshold", 30)
        print(f"[Regime] VIX={vix_last:.2f}, threshold={vix_thresh}  →  {'OK ✅' if vix_last < vix_thresh else 'HIGH ❌'}")

    # 5/14 시점 신호 (어제 daily_close 가 캐시로 산출했을 것)
    today_old = pd.Timestamp("2026-05-14")
    if today_old in (spy.index if spy is not None else []):
        print(f"\n=== 과거 시점 신호 (5/14, 어제 캐시 시점) ===")
        try:
            cl_old = get_clenow_signals(price_data, set(), cfg, today_old, is_wednesday=True)
            print(f"  Clenow buy:    {len(cl_old.get('buy', []))}건 → {cl_old.get('buy', [])[:10]}")
            ws_old = get_weinstein_signals(price_data, set(), cfg, today_old, is_wednesday=True)
            print(f"  Weinstein buy: {len(ws_old.get('buy', []))}건 → {ws_old.get('buy', [])[:10]}")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Clenow 신호 (is_wednesday=True 강제)
    print("\n=== Clenow 신호 (is_wednesday=True 강제) ===")
    try:
        cl = get_clenow_signals(price_data, set(), cfg, today, is_wednesday=True)
        print(f"  buy:        {len(cl.get('buy', []))}건 → {cl.get('buy', [])[:10]}")
        print(f"  sell_ma100: {len(cl.get('sell_ma100', []))}건 → {cl.get('sell_ma100', [])[:10]}")
        print(f"  sell_ranked:{len(cl.get('sell_ranked', []))}건 → {cl.get('sell_ranked', [])[:10]}")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()

    # Weinstein 신호
    print("\n=== Weinstein 신호 (is_wednesday=True 강제) ===")
    try:
        ws = get_weinstein_signals(price_data, set(), cfg, today, is_wednesday=True)
        print(f"  buy:       {len(ws.get('buy', []))}건 → {ws.get('buy', [])[:10]}")
        print(f"  sell_ma30: {len(ws.get('sell_ma30', []))}건 → {ws.get('sell_ma30', [])[:10]}")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
