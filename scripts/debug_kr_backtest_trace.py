"""
백테스트 엔진 내부 추적 — exec_date 마다 진입 분기에서 어디서 break 되는지 확인.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yaml

from src.fetch.universe import get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.strategy.clenow_momentum import compute_scores
from src.backtest.costs import make_cost_model
from src.markets import get_profile


def main():
    profile = get_profile("kr")
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code": profile.code, "regime_index": profile.index_ticker, "currency": profile.currency}

    cl_p = cfg.setdefault("clenow_strategy", {})
    cl_p["min_price"]    = profile.min_price
    cl_p["index_ticker"] = profile.index_ticker

    tickers = [profile.index_ticker] + get_kospi200_tickers()
    price_data = fetch_all(tickers, cfg["data"]["start_date"], cfg["data"]["end_date"],
                           min_bars=150, market="kr")
    print(f"loaded: {len(price_data)} symbols")

    cl_cfg = cl_p
    # KR 초기 자본 swap (run_clenow.py 와 동일 로직)
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    initial_capital = float(cfg["backtest"]["initial_capital_usd"])
    slippage = cfg["risk"]["slippage_pct"] / 100
    max_pos  = cl_cfg.get("max_positions", 20)
    ma200_p  = cl_cfg.get("spy_ma200_period", 200)
    cost_model = make_cost_model(cfg)

    spy_df    = price_data[profile.index_ticker]
    spy_close = spy_df["close"]
    spy_ma200 = spy_close.rolling(ma200_p).mean()
    all_dates = sorted(spy_df.index)
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    # weekly_dates
    weekly_dates = []
    seen = set()
    for d in all_dates:
        wk = (d.year, d.isocalendar()[1])
        if d.weekday() == 2:
            weekly_dates.append(d); seen.add(wk)
        elif d == all_dates[-1] and wk not in seen:
            weekly_dates.append(d)
    print(f"weekly_dates: {len(weekly_dates)} (first={weekly_dates[0].date()}, last={weekly_dates[-1].date()})")

    # exec_map
    exec_map = {}
    for wed in weekly_dates:
        i = date_idx[wed]
        for off in range(1, 6):
            if i + off < len(all_dates):
                exec_map[wed] = all_dates[i + off]; break

    # regime ON 첫 weekly_date 찾기
    regime_on_weds = []
    for wed in weekly_dates:
        i = date_idx[wed]
        spy_ma = spy_ma200.iloc[i]
        spy_c  = spy_close.iloc[i]
        regime_ok = not pd.isna(spy_ma) and spy_c > spy_ma
        if regime_ok:
            regime_on_weds.append(wed)
    print(f"regime_on weekly count: {len(regime_on_weds)}/{len(weekly_dates)}")
    print(f"first regime_on wed: {regime_on_weds[0].date() if regime_on_weds else None}")

    # 첫 regime_on 수요일 + exec_date 시뮬레이션
    test_wed = regime_on_weds[0]
    exec_date = exec_map[test_wed]
    print()
    print(f"=== 시뮬레이션: wed={test_wed.date()}, exec_date={exec_date.date()} ===")

    scores = compute_scores(price_data, test_wed, cl_cfg)
    print(f"scores 개수: {len(scores)}")
    if not scores:
        print("[STOP] compute_scores 빈 dict — 매수 후보 없음")
        return

    top = sorted(scores, key=lambda s: scores[s], reverse=True)[:max_pos]
    print(f"top_syms (max {max_pos}): {len(top)}")
    print(f"sample top 5: {top[:5]}")

    # 백테스트 엔진의 정확한 매수 흐름 재현
    cash = initial_capital
    holdings = {}
    n_slots = len(top)

    total_equity = cash   # holdings 비었으므로
    target_alloc = total_equity / n_slots
    print(f"target_alloc per slot: ${target_alloc:.2f}")

    pos_val_now = 0
    equity_now  = cash
    cap_cfg = cfg.get("capital_mgmt", {})
    max_entries_day   = cap_cfg.get("max_new_entries_day", 999)
    max_daily_pct     = cap_cfg.get("max_daily_invest_pct", 100) / 100
    target_invest_pct = cap_cfg.get("target_invested_pct", 100) / 100
    daily_budget = equity_now * max_daily_pct
    daily_spent  = 0.0
    entries_today = 0

    print(f"daily_budget: ${daily_budget:.2f}, target_invest_pct: {target_invest_pct}")

    # 매수 시뮬레이션 — break 사유 출력
    bought = 0
    for sym in top:
        if entries_today >= max_entries_day:
            print(f"  STOP at {sym}: entries_today >= max_entries_day"); break
        cur_invested = pos_val_now + daily_spent
        if equity_now > 0 and cur_invested / equity_now >= target_invest_pct:
            print(f"  STOP at {sym}: cur_invested/equity ({cur_invested/equity_now:.2f}) >= target ({target_invest_pct})"); break
        df_sym = price_data.get(sym)
        if df_sym is None or exec_date not in df_sym.index:
            print(f"  SKIP {sym}: no data on exec_date")
            continue
        open_px = df_sym.loc[exec_date, "open"]
        if pd.isna(open_px) or open_px <= 0:
            print(f"  SKIP {sym}: open_px invalid ({open_px})")
            continue
        buy_px = open_px * (1 + slippage)
        alloc  = min(target_alloc, cash * 0.98)
        if daily_spent + alloc > daily_budget:
            print(f"  SKIP {sym}: daily_spent({daily_spent}) + alloc({alloc}) > daily_budget({daily_budget})")
            continue
        if alloc < buy_px:
            print(f"  SKIP {sym}: alloc({alloc:.2f}) < buy_px({buy_px:.2f})")
            continue
        buy_comm = cost_model.buy_cost(alloc)
        if alloc + buy_comm > cash:
            alloc = cash / (1 + cost_model.commission_pct) * 0.98
            buy_comm = cost_model.buy_cost(alloc)
        shares = alloc / buy_px
        cash  -= alloc + buy_comm
        daily_spent  += alloc
        entries_today += 1
        bought += 1
        if bought <= 5:
            print(f"  BUY  {sym}: open={open_px:.0f}, buy_px={buy_px:.0f}, alloc={alloc:.0f}, shares={shares:.4f}")

    print(f"\n매수 성공: {bought}/{len(top)}")
    print(f"남은 현금: ${cash:.2f} / 초기: ${initial_capital:.2f}")


if __name__ == "__main__":
    main()
