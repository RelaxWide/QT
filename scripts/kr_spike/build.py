"""
KR 급등 패턴 연구 — 특징·결과 표 만들기.

전 종목(KOSPI·KOSDAQ 보통주, 우선주·스팩·리츠 제외), 5거래일마다 기준일. 각 (기준일, 종목)에 대해
  특징 : 그날 종가까지의 가격·거래량 정보만 (미래 정보 없음)
  결과 : 다음 거래일 종가 진입 후 60거래일 — 보유 수익, 기간 중 최고/최저, +50% 도달 여부, 익절·손절 규칙 수익
사용: python scripts/kr_spike/build.py <marcap 캐시 폴더>   → <폴더>/spike_panel.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kr_base"))
from base_study import adj_returns  # noqa: E402

H = 60


def load(cache: Path, y0=2003):
    cols = ["Code", "Name", "Date", "Open", "High", "Low", "Close", "ChangesRatio", "Volume", "Amount", "Marcap", "Stocks",
            "Market", "Dept"]
    d = pd.concat([pd.read_parquet(cache / f"marcap-{y}.parquet", columns=cols)
                   for y in range(y0, pd.Timestamp.today().year + 1) if (cache / f"marcap-{y}.parquet").exists()],
                  ignore_index=True)
    d["Code"] = d["Code"].astype(str)
    d["Date"] = pd.to_datetime(d["Date"])
    name = d["Name"].fillna("")
    keep = d["Code"].str.match(r"^\d{5}0$") & d["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])
    keep &= ~name.str.contains("스팩|기업인수목적|리츠|맥쿼리인프라")
    return d[keep].sort_values(["Code", "Date"]).reset_index(drop=True)


def main():
    cache = Path(sys.argv[1])
    d = load(cache)
    d["ret"] = adj_returns(d)
    d["turn"] = d["Volume"] / d["Stocks"].replace(0, np.nan)
    hl = (d["High"] - d["Low"]).replace(0, np.nan)
    d["clv"] = ((d["Close"] - d["Low"]) / hl).where(d["Volume"] > 0)          # 종가의 일중 위치 (1 = 고가 마감)
    d["uw"] = ((d["High"] - d[["Open", "Close"]].max(axis=1)) / d["Close"].replace(0, np.nan)).where(d["Volume"] > 0)
    d["mgmt"] = d["Dept"].fillna("").str.contains("관리종목|투자주의환기").astype(np.float32)
    dates = pd.DatetimeIndex(sorted(d["Date"].unique()))
    codes = pd.Index(sorted(d["Code"].unique()))
    r = pd.Series(np.arange(len(dates)), index=dates)[d["Date"]].to_numpy()
    c = pd.Series(np.arange(len(codes)), index=codes)[d["Code"]].to_numpy()

    def wide(col):
        m = np.full((len(dates), len(codes)), np.nan, dtype=np.float32)
        m[r, c] = d[col].to_numpy()
        return m

    R = wide("ret")
    listed = ~np.isnan(R)
    vol = wide("Volume")
    trad = listed & (vol > 0)
    P = np.where(listed, np.log1p(np.nan_to_num(R)), 0.0).cumsum(0).astype(np.float32)
    Pm = pd.DataFrame(np.where(listed, P, np.nan))
    Rd = pd.DataFrame(np.where(trad, R, np.nan))
    T = pd.DataFrame(wide("turn"))
    last = np.where(listed.any(0), len(dates) - 1 - listed[::-1].argmax(0), -1)
    first = np.where(listed.any(0), listed.argmax(0), len(dates))

    forms = [t for t in range(0, len(dates) - H - 2, 5) if dates[t] >= pd.Timestamp("2007-01-01")]
    F = {}

    def keep(name, frame):                                       # 기준일 행만 남기고 버린다
        F[name] = (frame.to_numpy() if isinstance(frame, pd.DataFrame) else frame)[forms].astype(np.float32)

    for k in (5, 20, 60, 120, 250):
        keep(f"r{k}", Pm - Pm.shift(k))
    hi250, lo250 = Pm.rolling(250, min_periods=200).max(), Pm.rolling(250, min_periods=200).min()
    keep("d_hi250", Pm - hi250); keep("d_lo250", Pm - lo250)
    hi500, lo500 = Pm.rolling(500, min_periods=480).max(), Pm.rolling(500, min_periods=480).min()
    keep("range500", hi500 - lo500); keep("pos500", (Pm - lo500) / (hi500 - lo500).clip(lower=1e-6))
    keep("dd5y", Pm - Pm.rolling(1250, min_periods=500).max())
    del hi250, lo250, hi500, lo500
    v20, v60, v250 = Rd.rolling(20, min_periods=15).std(), Rd.rolling(60, min_periods=40).std(), Rd.rolling(250, min_periods=200).std()
    keep("vol20", v20); keep("vol250", v250); keep("vc20", v20 / v250); keep("vc60", v60 / v250)
    del v20, v60
    keep("max1d20", Rd.rolling(20, min_periods=15).max()); keep("min1d20", Rd.rolling(20, min_periods=15).min())
    big = (Rd >= 0.15).astype(np.float32)
    keep("nbig20", big.rolling(20, min_periods=15).sum()); keep("nbig250", big.rolling(250, min_periods=200).sum())
    del big
    t5, t20, t60, t250 = (T.rolling(k, min_periods=int(k * .7)).mean() for k in (5, 20, 60, 250))
    keep("tv5", t5 / t250); keep("tv20", t20 / t250); keep("tv60", t60 / t250); keep("turn250", t250)
    keep("maxtv20", T.rolling(20, min_periods=15).max() / t250)
    del t5, t20, t60
    up = T.where(Rd > 0, 0.0)
    keep("upvol20", up.rolling(20, min_periods=15).sum() / T.rolling(20, min_periods=15).sum().replace(0, np.nan))
    keep("upvol60", up.rolling(60, min_periods=40).sum() / T.rolling(60, min_periods=40).sum().replace(0, np.nan))
    del up
    keep("clv20", pd.DataFrame(wide("clv")).rolling(20, min_periods=12).mean())
    keep("uw20", pd.DataFrame(wide("uw")).rolling(20, min_periods=12).mean())
    cap, amt = wide("Marcap"), wide("Amount")
    keep("lcap", np.log(np.where(cap > 0, cap, np.nan))); keep("lprice", np.log(np.where(wide("Close") > 0, wide("Close"), np.nan)))
    keep("lamt60", np.log(pd.DataFrame(amt).rolling(60, min_periods=40).mean().replace(0, np.nan)))
    keep("zero20", pd.DataFrame((listed & ~trad).astype(np.float32)).rolling(20, min_periods=15).sum())
    keep("mgmt", wide("mgmt"))
    del cap, amt, T, Rd, t250

    rows = []
    for i, t in enumerate(forms):
        e = t + 1
        ok = trad[t] & trad[e] & ~np.isnan(F["r250"][i]) & ~np.isnan(F["vol250"][i])
        idx = np.where(ok)[0]
        if len(idx) < 50:
            continue
        rec = {"t": t, "code": idx, "age": (t - first[idx]).astype(np.float32)}
        for k, m in F.items():
            rec[k] = m[i, idx]
        rec["mkt_r60"] = np.float32(np.nanmean(F["r60"][i, idx]))
        rec["mkt_r250"] = np.float32(np.nanmean(F["r250"][i, idx]))
        win = P[e + 1: e + 1 + H, idx] - P[e, idx]
        rec["ret60"] = np.expm1(win[-1]); rec["ret20"] = np.expm1(win[19])
        rec["max60"] = np.expm1(win.max(0)); rec["min60"] = np.expm1(win.min(0))
        rec["del60"] = last[idx] < e + H
        for nm, tp, sl in (("r_tp50_sl20", .5, -.2), ("r_tp30_sl15", .3, -.15), ("r_tp50", .5, -9.0)):
            hit_tp, hit_sl = win >= np.log1p(tp), win <= np.log1p(max(sl, -0.999))
            ftp = np.where(hit_tp.any(0), hit_tp.argmax(0), H + 1)
            fsl = np.where(hit_sl.any(0), hit_sl.argmax(0), H + 1)
            ex = np.minimum(np.minimum(ftp, fsl), H - 1)
            rec[nm] = np.expm1(win[ex, np.arange(len(idx))])          # 닿은 날 종가로 청산 (갭이면 그 종가 그대로)
        rows.append(pd.DataFrame(rec))
    ev = pd.concat(rows, ignore_index=True)
    ev["date"] = dates[ev["t"].to_numpy()]
    ev.to_parquet(cache / "spike_panel.parquet", index=False)
    print(f"저장 {len(ev):,}행, 기준일 {ev['t'].nunique()}개 ({ev['date'].min().date()}~{ev['date'].max().date()}), 종목 {ev['code'].nunique()}개, 열 {ev.shape[1]}")


if __name__ == "__main__":
    main()
