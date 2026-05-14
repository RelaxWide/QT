"""
KIS Live 스모크 테스트 - 매수 후보 생성

페이퍼 상태와 무관하게 현재 시점의 Clenow / Weinstein 신호에서 가격이
스모크 테스트 예산에 맞는 후보를 골라 live_trading/wed_buy_pending.json 에 저장한다.

사용:
    python scripts/smoke_test_setup.py [--clenow-budget 60] [--weinstein-budget 40]

이후:
    python scheduler/wednesday_morning_buy.py --no-wait
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.strategy.clenow_momentum import compute_scores
from src.strategy.weinstein_stage2 import generate_weinstein_signals

WED_BUY_PENDING = Path("live_trading/wed_buy_pending.json")


def main(clenow_budget: float, weinstein_budget: float, weinstein_lookback_days: int):
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    tickers = get_sp500_tickers()
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=150)
    print(f"[smoke_setup] {len(price_data)} 종목 로드")

    latest_dates = [df.index.max() for df in price_data.values() if not df.empty]
    if not latest_dates:
        print("[smoke_setup] 가격 데이터 없음 - 종료")
        return
    today = max(latest_dates)
    print(f"[smoke_setup] data_today = {today.date()}")

    # ── Clenow: 모멘텀 상위 중 예산 fit ───────────────────────────────────
    cl_cfg = cfg.get("clenow_strategy", {})
    scores = compute_scores(price_data, today, cl_cfg)
    top_clenow = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    print(f"[smoke_setup] Clenow 모멘텀 상위 10: {[s for s,_ in top_clenow[:10]]}")

    clenow_picks = []
    for sym, _ in top_clenow:
        if sym not in price_data or today not in price_data[sym].index:
            continue
        px = float(price_data[sym].at[today, "close"])
        if px > 0 and px * 1.005 <= clenow_budget:
            clenow_picks.append(sym)
            if len(clenow_picks) >= 5:
                break
    print(f"[smoke_setup] Clenow 가격 fit 후보 (≤ ${clenow_budget:.0f}): {clenow_picks}")

    # ── Weinstein: 최근 Stage 2 돌파 중 예산 fit ──────────────────────────
    w_cfg = cfg.get("weinstein_strategy", {})
    cutoff = today - pd.Timedelta(days=weinstein_lookback_days)
    weinstein_picks = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        try:
            sigs = generate_weinstein_signals(sym, df, w_cfg)
        except Exception:
            continue
        if not sigs:
            continue
        recent = [s for s in sigs if s.signal_week >= cutoff]
        if not recent:
            continue
        if today not in df.index:
            continue
        px = float(df.at[today, "close"])
        if px > 0 and px * 1.005 <= weinstein_budget:
            weinstein_picks.append(sym)
            if len(weinstein_picks) >= 5:
                break
    print(f"[smoke_setup] Weinstein 최근 {weinstein_lookback_days}일 Stage 2 + 가격 fit (≤ ${weinstein_budget:.0f}): {weinstein_picks}")

    # ── pending 저장 ─────────────────────────────────────────────────────
    payload = {}
    if clenow_picks:
        payload["clenow"] = {
            "signal_date": str(today.date()),
            "symbols":     clenow_picks,
        }
    if weinstein_picks:
        payload["weinstein"] = {
            "signal_date": str(today.date()),
            "symbols":     weinstein_picks,
        }

    if not payload:
        print("[smoke_setup] 매수 후보 0건 - pending 생성 안 함")
        return

    WED_BUY_PENDING.parent.mkdir(exist_ok=True)
    WED_BUY_PENDING.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    print(f"[smoke_setup] pending 저장: {WED_BUY_PENDING}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--clenow-budget",    type=float, default=60.0)
    p.add_argument("--weinstein-budget", type=float, default=40.0)
    p.add_argument("--weinstein-lookback-days", type=int, default=30,
                   help="Weinstein 최근 N일 Stage 2 신호 허용 (스모크 테스트라 기본 30일로 완화)")
    args = p.parse_args()
    main(args.clenow_budget, args.weinstein_budget, args.weinstein_lookback_days)
