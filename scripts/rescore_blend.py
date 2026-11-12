"""
기존 전략 재채점 — SPY+Clenow 조합에 얹었을 때 나아지는가 (기준: docs/RESCORE.md 1절).

    python scripts/rescore_blend.py

입력: backtest_results/**/*_equity.csv (KR 제외), data/raw_extended/SPY.parquet (없으면 data/raw)
출력: backtest_results/rescore/{sweep,candidates,families}.csv + 콘솔 요약
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RES = Path("backtest_results")
OUT = RES / "rescore"
MAIN_START, MAIN_END = "2015-01-02", "2026-04-30"
SPLIT = "2020-07-01"
BASE_W_CLENOW = 0.5            # 기준선 B = SPY 50 : Clenow 50
WEIGHTS = (0.10, 0.20, 0.30)
JUDGE_W = 0.20
MIN_DSHARPE, MAX_DMDD, MAX_DCAGR = 0.05, -1.0, -1.0   # %p
LONG_MAX_START = "2007-12-31"  # 이보다 일찍 시작하는 곡선만 "장기"로 인정


def load_equity(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"]).dropna().drop_duplicates("date").set_index("date")
    return df["equity"].astype(float).sort_index()


def load_spy() -> pd.Series:
    for p in (Path("data/raw_extended/SPY.parquet"), Path("data/raw/SPY.parquet")):
        if p.exists():
            s = pd.read_parquet(p)["close"].astype(float)
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
    sys.exit("SPY 가격 캐시가 없음")


def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 60:
        return {"cagr": np.nan, "sharpe": np.nan, "mdd": np.nan}
    eq = (1 + r).cumprod()
    years = len(r) / 252
    return {"cagr": (eq.iloc[-1] ** (1 / years) - 1) * 100,
            "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan,
            "mdd": (eq / eq.cummax() - 1).min() * 100}


def rets(levels: dict[str, pd.Series], start: str, end: str) -> pd.DataFrame:
    """공통 거래일로 맞춘 일수익률."""
    df = pd.concat(levels, axis=1, join="inner").loc[start:end]
    return df.pct_change().dropna()


def family_of(name: str) -> str:
    for pat, fam in ((r"^phase4_v2", "Phase 4 v2 (선행 구름)"), (r"^phase4_v3", "Phase 4 v3 (필터 변형)"),
                     (r"^phase4$", "Phase 4 원형"), (r"^phase[23]$", "Phase 2·3"),
                     (r"^haa_", "HAA"), (r"^gem_", "GEM"), (r"^gtaa", "GTAA-5"),
                     (r"^faber", "Faber TAA"), (r"^monthly_momentum", "월간 모멘텀 ETF"),
                     (r"^weinstein", "Weinstein"), (r"^high52", "52주 신고가"), (r"^highwr", "MA 눌림목 반등"),
                     (r"^(alvarez|double7|ibs|rsi2_spy|vix_mr)", "단기 평균회귀"),
                     (r"insider", "내부자매수")):
        if re.search(pat, name):
            return fam
    return name


def evaluate(x: pd.Series, spy: pd.Series, cl: pd.Series, start: str, end: str) -> dict | None:
    R = rets({"spy": spy, "cl": cl, "x": x}, start, end)
    if len(R) < 252:
        return None
    base = (1 - BASE_W_CLENOW) * R["spy"] + BASE_W_CLENOW * R["cl"]
    mb = metrics(base)
    out = {"n_days": len(R), "start": str(R.index[0].date()), "end": str(R.index[-1].date()),
           "base_sharpe": mb["sharpe"], "base_mdd": mb["mdd"], "base_cagr": mb["cagr"],
           "corr_base": R["x"].corr(base)}
    out.update({f"x_{k}": v for k, v in metrics(R["x"]).items()})
    for w in WEIGHTS:
        m = metrics((1 - w) * base + w * R["x"])
        t = int(w * 100)
        out[f"dS{t}"], out[f"dMDD{t}"], out[f"dCAGR{t}"] = m["sharpe"] - mb["sharpe"], m["mdd"] - mb["mdd"], m["cagr"] - mb["cagr"]
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    spy = load_spy()
    clenow, clenow_ext = load_equity(RES / "clenow_equity.csv"), load_equity(RES / "clenow_ext_equity.csv")

    # ── 1. SPY:Clenow 비율 스윕 ───────────────────────────────────────────
    rows = []
    for label, cl, s, e in (("2015~", clenow, MAIN_START, MAIN_END), ("2000~", clenow_ext, "2000-01-03", MAIN_END),
                            ("2015-01~2020-06", clenow, MAIN_START, "2020-06-30"), ("2020-07~", clenow, SPLIT, MAIN_END)):
        R = rets({"spy": spy, "cl": cl}, s, e)
        for wc in np.arange(0, 1.01, 0.1):
            m = metrics((1 - wc) * R["spy"] + wc * R["cl"])
            rows.append({"period": label, "clenow_pct": int(round(wc * 100)), **{k: round(v, 2) for k, v in m.items()},
                         "corr_spy_clenow": round(R["spy"].corr(R["cl"]), 2)})
    sweep = pd.DataFrame(rows)
    sweep.to_csv(OUT / "sweep.csv", index=False)

    # ── 2. 후보별 혼합 ───────────────────────────────────────────────────
    files = [f for f in sorted(RES.rglob("*_equity.csv"))
             if "kr" not in f.parts and "rescore" not in f.parts and not f.name.startswith("clenow")]
    recs = []
    for f in files:
        name = f.name.replace("_equity.csv", "")
        x = load_equity(f)
        full = evaluate(x, spy, clenow, MAIN_START, MAIN_END)
        if full is None:
            continue
        h1 = evaluate(x, spy, clenow, MAIN_START, "2020-06-30")
        h2 = evaluate(x, spy, clenow, SPLIT, MAIN_END)
        rec = {"name": name, "family": family_of(name), **full,
               "h1_dS20": h1["dS20"] if h1 else np.nan, "h2_dS20": h2["dS20"] if h2 else np.nan,
               "long_dS20": np.nan, "long_start": ""}
        # 장기: 자기 곡선이 일찍 시작하면 그대로, 아니면 *_ext 짝 (weinstein → weinstein_ext, phase4_v3_X → phase4_v3_ext_X)
        ext_name = name if x.index[0] <= pd.Timestamp(LONG_MAX_START) else (
            name.replace("phase4_v3_", "phase4_v3_ext_") if name.startswith("phase4_v3_") else name + "_ext")
        ext_file = f.parent / f"{ext_name}_equity.csv"
        if ext_file.exists():
            xl = load_equity(ext_file)
            if xl.index[0] <= pd.Timestamp(LONG_MAX_START):
                lg = evaluate(xl, spy, clenow_ext, str(xl.index[0].date()), MAIN_END)
                if lg:
                    rec["long_dS20"], rec["long_start"] = lg["dS20"], lg["start"]
        t = int(JUDGE_W * 100)
        c1 = rec[f"dS{t}"] >= MIN_DSHARPE and rec[f"dMDD{t}"] >= MAX_DMDD and rec[f"dCAGR{t}"] >= MAX_DCAGR
        c2 = rec["h1_dS20"] >= 0 and rec["h2_dS20"] >= 0
        c3 = rec["dS10"] >= 0 and rec["dS30"] >= 0
        c4 = rec["long_dS20"] >= MIN_DSHARPE if not np.isnan(rec["long_dS20"]) else None
        rec.update({"c1_full": c1, "c2_halves": c2, "c3_weights": c3, "c4_long": c4,
                    "verdict": ("통과" if c4 else "조건부(장기 미검증)" if c4 is None else "탈락(장기)")
                               if (c1 and c2 and c3) else "탈락"})
        recs.append(rec)
    cand = pd.DataFrame(recs)
    cand.to_csv(OUT / "candidates.csv", index=False)

    # ── 3. 계열 요약 (중앙값 변형으로 판정) ───────────────────────────────
    fam_rows = []
    for fam, g in cand.groupby("family"):
        g = g[~g["name"].str.contains("_ext")] if (~g["name"].str.contains("_ext")).any() else g
        med = g.sort_values("dS20").iloc[len(g) // 2]
        best = g.sort_values("dS20").iloc[-1]
        fam_rows.append({"family": fam, "n_variants": len(g), "n_pass": int((g["verdict"] == "통과").sum()),
                         "n_conditional": int(g["verdict"].str.startswith("조건부").sum()),
                         "median_variant": med["name"], "median_dS20": med["dS20"], "median_verdict": med["verdict"],
                         "best_variant": best["name"], "best_dS20": best["dS20"], "best_verdict": best["verdict"]})
    fam = pd.DataFrame(fam_rows).sort_values("median_dS20", ascending=False)
    fam.to_csv(OUT / "families.csv", index=False)

    pd.set_option("display.width", 250, "display.max_rows", 200, "display.max_columns", 40)
    print("\n=== SPY:Clenow 스윕 ===")
    print(sweep.pivot(index="clenow_pct", columns="period", values="sharpe").to_string())
    print(sweep.pivot(index="clenow_pct", columns="period", values="mdd").to_string())
    print(sweep.pivot(index="clenow_pct", columns="period", values="cagr").to_string())
    print(sweep.groupby("period")["corr_spy_clenow"].first().to_string())
    b = cand.iloc[0]
    print(f"\n기준선 B(50:50) {b['start']}~: Sharpe {b['base_sharpe']:.2f} / MDD {b['base_mdd']:.1f}% / CAGR {b['base_cagr']:.1f}%")
    print("\n=== 계열 요약 ===")
    print(fam.round(3).to_string(index=False))
    cols = ["name", "x_cagr", "x_sharpe", "x_mdd", "corr_base", "dS10", "dS20", "dS30", "dMDD20", "dCAGR20",
            "h1_dS20", "h2_dS20", "long_dS20", "verdict"]
    print("\n=== 후보 상위 25 (ΔSharpe@20%) ===")
    print(cand.sort_values("dS20", ascending=False)[cols].head(25).round(3).to_string(index=False))
    print("\n판정 분포:", cand["verdict"].value_counts().to_dict())


if __name__ == "__main__":
    main()
