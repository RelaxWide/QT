"""
아이디어 1(지수 낙폭 사다리)·2(SPY↔Clenow 갈아타기) 백테스트. 기준·파라미터: docs/IDEAS_BACKTEST.md 1절 (고정).

    python scripts/backtest_ideas.py

입력: data/cache/research/{_GSPC,_IRX,SPY,SSO}.csv  (GitHub Actions "Research Data Fetch" → research-data 브랜치)
      backtest_results/clenow_equity.csv, clenow_ext_equity.csv
출력: backtest_results/ideas/*.csv + 콘솔 요약
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/cache/research")
RES = Path("backtest_results")
OUT = RES / "ideas"

LADDERS = {                      # (낙폭 기준, 포트폴리오 대비 옮기는 비중)
    "A 보수": [(-0.15, 0.15), (-0.25, 0.15)],
    "B 기본": [(-0.15, 0.25), (-0.25, 0.25)],
    "C 공격": [(-0.15, 0.30), (-0.25, 0.30), (-0.35, 0.40)],
}
MOVE_COST = 0.005                # 옮기는 금액 기준: 매도 0.25% + 매수 0.25%
TAX = 0.22
SPREAD, FEE = 0.005, 0.009       # 2배 레버리지 조달 스프레드, 보수 (연)
PRE1960_RF = 2.0                 # % — ^IRX 이전 구간 가정
SWITCH_COST = 0.005


def load(name: str, col: str) -> pd.Series:
    df = pd.read_csv(DATA / f"{name}.csv", parse_dates=["date"], index_col="date")
    return df[col].astype(float).dropna().sort_index()


def metrics(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    years = len(r) / 252
    return {"cagr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100,
            "mdd": (eq / eq.cummax() - 1).min() * 100,
            "sharpe": r.mean() / r.std() * np.sqrt(252),
            "mult": eq.iloc[-1] / eq.iloc[0]}


def lev2_returns(r_idx: pd.Series, rf_pct: pd.Series) -> pd.Series:
    rf = rf_pct.reindex(r_idx.index).ffill().fillna(PRE1960_RF) / 100
    return (2 * r_idx - (rf + SPREAD) / 252 - FEE / 252).clip(lower=-0.999)


def run_ladder(level: pd.Series, rf_pct: pd.Series, steps: list, tax: bool):
    """level: 지수 수준(일별). 반환: (포트폴리오 가치 Series, 에피소드 목록)."""
    r_idx = level.pct_change().fillna(0.0)
    r_lev = lev2_returns(r_idx, rf_pct)
    dd = (level / level.cummax() - 1).values
    ri, rl = r_idx.values, r_lev.values
    v_idx, v_lev, basis = 1.0, 0.0, 0.0
    done: set[int] = set()
    pending: list = []            # 전일 종가 신호 → 오늘 종가 체결
    out = np.empty(len(level))
    episodes, cur = [], None
    for t in range(len(level)):
        v_idx *= 1 + ri[t]
        v_lev *= 1 + rl[t]
        for kind, frac in pending:
            if kind == "in":
                amt = min(frac * (v_idx + v_lev), v_idx)
                v_idx -= amt
                v_lev += amt * (1 - MOVE_COST)
                basis += amt
            else:                                   # 전부 지수로 복귀
                proceeds = v_lev * (1 - MOVE_COST)
                if tax:
                    proceeds -= TAX * max(0.0, v_lev - basis)
                if cur is not None:
                    cur.update(exit=level.index[t], lev_ret=(v_lev / basis - 1) * 100 if basis else 0.0)
                    episodes.append(cur)
                    cur = None
                v_idx += proceeds
                v_lev, basis = 0.0, 0.0
                done.clear()
        pending = []
        for k, (thr, frac) in enumerate(steps):
            if dd[t] <= thr and k not in done:
                done.add(k)
                pending.append(("in", frac))
                if cur is None:
                    cur = {"entry": level.index[t], "v0": v_idx + v_lev, "lvl0": level.iloc[t], "depth": dd[t]}
        if cur is not None:
            cur["depth"] = min(cur["depth"], dd[t])
        if dd[t] == 0 and (v_lev > 0 or done) and not any(p[0] == "in" for p in pending):
            pending.append(("out", None))
        out[t] = v_idx + v_lev
    if cur is not None:
        cur.update(exit=None, lev_ret=(v_lev / basis - 1) * 100 if basis else 0.0)
        episodes.append(cur)
    eq = pd.Series(out, index=level.index)
    for e in episodes:                              # 에피소드 동안 지수 보유 대비 초과
        end = e["exit"] if e["exit"] is not None else level.index[-1]
        e["excess_pct"] = ((eq[end] / e["v0"]) / (level[end] / e["lvl0"]) - 1) * 100
    return eq, episodes


def idea1():
    gspc, irx = load("_GSPC", "close"), load("_IRX", "close")
    spy, sso = load("SPY", "adj_close"), load("SSO", "adj_close")

    # 모형 검증: 합성 2배 vs 실제 SSO
    r_syn = lev2_returns(spy.pct_change().dropna(), irx)
    both = pd.concat({"syn": (1 + r_syn).cumprod(), "sso": sso}, axis=1, join="inner").dropna()
    both = both / both.iloc[0]
    v = {k: metrics(both[k]) for k in both}
    print(f"\n[2배 모형 검증 {both.index[0].date()}~{both.index[-1].date()}] 합성 CAGR {v['syn']['cagr']:.2f}% / MDD {v['syn']['mdd']:.1f}%"
          f"  vs  실제 SSO CAGR {v['sso']['cagr']:.2f}% / MDD {v['sso']['mdd']:.1f}%")

    rows, ep_rows = [], []
    universes = {"S&P500 지수 1950~ (배당 제외)": gspc["1950":], "S&P500 지수 1928~ (대공황 포함)": gspc,
                 "SPY 1993~ (배당 포함)": spy}
    periods = {"S&P500 지수 1950~ (배당 제외)": [("전체", None, None), ("1950-1974", "1950", "1974"),
                                           ("1975-1999", "1975", "1999"), ("2000-현재", "2000", None)],
               "S&P500 지수 1928~ (대공황 포함)": [("전체", None, None), ("1928-1949", None, "1949")],
               "SPY 1993~ (배당 포함)": [("전체", None, None)]}
    for uname, level in universes.items():
        base = level / level.iloc[0]
        for name, steps in LADDERS.items():
            for tax in (False, True):
                eq, eps = run_ladder(level, irx, steps, tax)
                for pname, s, e in periods[uname]:
                    m, mb = metrics(eq.loc[s:e]), metrics(base.loc[s:e])
                    rows.append({"universe": uname, "ladder": name, "tax": tax, "period": pname,
                                 "cagr": m["cagr"], "idx_cagr": mb["cagr"], "d_cagr": m["cagr"] - mb["cagr"],
                                 "mdd": m["mdd"], "idx_mdd": mb["mdd"], "d_mdd": m["mdd"] - mb["mdd"],
                                 "sharpe": m["sharpe"], "idx_sharpe": mb["sharpe"], "n_episodes": len(eps)})
                if name.startswith("B") and tax:
                    for x in eps:
                        ep_rows.append({"universe": uname, "entry": x["entry"].date(),
                                        "exit": x["exit"].date() if x["exit"] is not None else "진행 중",
                                        "max_depth_pct": x["depth"] * 100, "lev_leg_ret_pct": x["lev_ret"],
                                        "excess_vs_index_pct": x["excess_pct"]})
    res, eps = pd.DataFrame(rows), pd.DataFrame(ep_rows)
    res.to_csv(OUT / "ladder.csv", index=False)
    eps.to_csv(OUT / "ladder_episodes_B_tax.csv", index=False)
    cols = ["ladder", "tax", "period", "cagr", "idx_cagr", "d_cagr", "mdd", "idx_mdd", "d_mdd", "sharpe", "idx_sharpe"]
    for uname in universes:
        print(f"\n=== 아이디어 1 — {uname} ===")
        print(res[res.universe == uname][cols].round(2).to_string(index=False))
    print("\n=== 에피소드 (조합 B, 세후) ===")
    print(eps.round(1).to_string(index=False))
    return res


def idea2():
    spy = load("SPY", "adj_close")
    rows = []
    for label, f, start in (("2000~", "clenow_ext_equity.csv", "2000-01-03"), ("2015~", "clenow_equity.csv", "2015-01-02")):
        cl = pd.read_csv(RES / f, parse_dates=["date"]).set_index("date")["equity"].astype(float)
        lv = pd.concat({"spy": spy, "cl": cl}, axis=1, join="inner").loc[start:].dropna()
        r = lv.pct_change().fillna(0.0)
        me = lv.groupby(lv.index.to_period("M")).tail(1)             # 월말 수준
        results = {}
        for L in (6, 12):
            trail = me / me.shift(L) - 1
            pick = (trail["cl"] > trail["spy"]).where(trail["cl"].notna())    # True = Clenow
            hold = pick.reindex(lv.index).ffill().shift(1)             # 다음 거래일부터 적용
            valid = hold.notna()
            h = hold[valid].astype(bool)
            ret = np.where(h, r["cl"][valid], r["spy"][valid])
            sw = h.ne(h.shift(1)).fillna(False)
            sw.iloc[0] = False
            ret = ret - sw.values * SWITCH_COST
            results[L] = (pd.Series(ret, index=h.index), int(sw.sum()), float(h.mean()))
        common = results[12][0].index                                 # 두 L 모두 같은 구간에서 비교
        bench = {"SPY": r["spy"].loc[common], "Clenow": r["cl"].loc[common],
                 "50:50": (0.5 * r["spy"] + 0.5 * r["cl"]).loc[common]}
        for name, x in bench.items():
            rows.append({"period": label, "strategy": name, **metrics((1 + x).cumprod()), "switches": 0, "pct_in_clenow": np.nan})
        for L, (x, n, pc) in results.items():
            x = x.loc[common]
            rows.append({"period": label, "strategy": f"갈아타기 L={L}", **metrics((1 + x).cumprod()),
                         "switches": n, "pct_in_clenow": pc * 100})
        print(f"  [{label}] 비교 구간 {common[0].date()} ~ {common[-1].date()}")
    res = pd.DataFrame(rows).drop(columns="mult")
    res.to_csv(OUT / "switch.csv", index=False)
    print("\n=== 아이디어 2 — SPY↔Clenow 갈아타기 ===")
    print(res.round(2).to_string(index=False))
    return res


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220, "display.max_rows", 200)
    idea1()
    idea2()
